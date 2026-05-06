const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { writeJsonFile } = require('../storage/projectStorage');

const DEFAULT_TIMEOUT_MS = 12000;
const DEFAULT_MAX_DOMAIN_CANDIDATES = 5;
const DEFAULT_MAX_PAGE_CANDIDATES = 3;
const DEFAULT_MAX_SOURCES_PER_TARGET = 3;
const DEFAULT_VERIFIED_CONFIDENCE = 0.85;
const FETCH_USER_AGENT =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

class OfficialSourceResolverNodeError extends Error {
  constructor(message, partialUpdate, cause) {
    super(message);
    this.name = 'OfficialSourceResolverNodeError';
    this.partialUpdate = partialUpdate;
    this.cause = cause;
  }
}

function isoNow() {
  return new Date().toISOString();
}

function slugify(value) {
  return String(value || '')
    .normalize('NFKD')
    .replace(/[^\w\s-]/g, ' ')
    .trim()
    .toLowerCase()
    .replace(/[_\s]+/g, '-')
    .replace(/-+/g, '-');
}

function uniq(values) {
  return [...new Set(values.filter(Boolean))];
}

function normalizeHost(value) {
  return String(value || '').trim().toLowerCase().replace(/^www\./, '');
}

function safeUrl(input) {
  try {
    return new URL(input);
  } catch {
    return null;
  }
}

function clampConfidence(value) {
  return Math.max(0, Math.min(1, Number(value.toFixed(2))));
}

function compactWhitespace(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function tokenize(value) {
  return compactWhitespace(value)
    .toLowerCase()
    .split(/[\s/-]+/)
    .map((token) => token.trim())
    .filter((token) => token.length >= 2);
}

function buildStep(stepName, status, startedAt, finishedAt, errorMessage) {
  const step = {
    step_name: stepName,
    status,
    started_at: startedAt,
  };

  if (finishedAt) {
    step.finished_at = finishedAt;
  }

  if (errorMessage) {
    step.error_message = errorMessage;
  }

  return step;
}

function buildResolutionTargets(state) {
  const targets = [];

  if (state?.own_product?.brand && state?.own_product?.name && state?.own_product?.category) {
    targets.push({
      target_id: 'own_product',
      target_type: 'own_product',
      brand: state.own_product.brand,
      product_name: state.own_product.name,
      category: state.own_product.category,
    });
  }

  for (const candidate of state?.competitor_candidates || []) {
    if (!candidate?.candidate_id || !candidate?.brand || !candidate?.product_name || !candidate?.category) {
      continue;
    }

    targets.push({
      target_id: candidate.candidate_id,
      target_type: 'competitor_candidate',
      brand: candidate.brand,
      product_name: candidate.product_name,
      category: candidate.category,
    });
  }

  return targets;
}

function buildKnownDomainMap(knownOfficialDomains = []) {
  return knownOfficialDomains.reduce((map, item) => {
    if (!item?.brand || !Array.isArray(item.domains)) {
      return map;
    }

    map.set(item.brand.trim().toLowerCase(), uniq(item.domains.map(normalizeHost)));
    return map;
  }, new Map());
}

function regionTlds(geography) {
  switch (String(geography || '').trim().toUpperCase()) {
    case 'KR':
      return ['co.kr', 'kr', 'com'];
    case 'JP':
      return ['co.jp', 'jp', 'com'];
    case 'US':
    case 'GLOBAL':
    default:
      return ['com', 'io', 'net'];
  }
}

function buildSearchQueries(target, state) {
  const queries = [
    `${target.brand} ${target.product_name} official`,
    `${target.brand} ${target.product_name} 공식`,
    `${target.brand} official site`,
  ];

  if (state?.geography) {
    queries.push(`${target.brand} ${target.product_name} ${state.geography} official`);
  }

  return uniq(queries);
}

function brandTokens(target) {
  return uniq([
    ...tokenize(target.brand),
    ...tokenize(target.brand).map((token) => token.replace(/[^a-z0-9가-힣]/g, '')),
  ]).filter(Boolean);
}

function productTokens(target) {
  const fullTokens = tokenize(target.product_name);
  const brandOnly = new Set(tokenize(target.brand));
  const withoutBrand = fullTokens.filter((token) => !brandOnly.has(token));
  return uniq(withoutBrand.length > 0 ? withoutBrand : fullTokens);
}

function domainLooksBrandOwned(domain, target) {
  const normalized = normalizeHost(domain);
  return brandTokens(target).some((token) => normalized.includes(token));
}

function buildHeuristicDomains(target, geography) {
  const base = slugify(target.brand).replace(/-/g, '');
  const tlds = regionTlds(geography);

  return uniq(
    tlds.flatMap((tld) => {
      return [
        `${base}.${tld}`,
        `${slugify(target.brand)}.${tld}`.replace(/--+/g, '-'),
      ];
    })
  );
}

function extractDomainFromUrl(input) {
  const parsed = safeUrl(input);
  return parsed ? normalizeHost(parsed.hostname) : null;
}

function isRejectedDomain(domain, rejectDomains) {
  const normalized = normalizeHost(domain);
  return rejectDomains.some((rejectDomain) => normalized === rejectDomain || normalized.endsWith(`.${rejectDomain}`));
}

async function discoverDomainsForTarget(target, state, options) {
  const rejectDomains = (state?.source_preferences?.reject_domains || []).map(normalizeHost);
  const knownDomainMap = buildKnownDomainMap(state?.known_official_domains || []);
  const maxCandidates =
    state?.search_context?.max_domain_candidates_per_target || DEFAULT_MAX_DOMAIN_CANDIDATES;
  const searchQueries = buildSearchQueries(target, state);
  const candidateDomains = [];
  const pageSeeds = [];
  const seenDomains = new Set();

  function pushDomainCandidate(candidate) {
    const normalized = normalizeHost(candidate.domain);
    if (!normalized || seenDomains.has(normalized)) {
      return;
    }

    seenDomains.add(normalized);
    candidateDomains.push({
      domain: normalized,
      source: candidate.source,
      is_brand_owned_candidate: Boolean(candidate.isBrandOwnedCandidate),
      selection_reason: candidate.selectionReason,
      ...(candidate.rejectionReason ? { rejection_reason: candidate.rejectionReason } : {}),
    });
  }

  const knownDomains = knownDomainMap.get(String(target.brand).trim().toLowerCase()) || [];
  for (const domain of knownDomains) {
    const rejected = isRejectedDomain(domain, rejectDomains);
    pushDomainCandidate({
      domain,
      source: 'known_domain',
      isBrandOwnedCandidate: !rejected,
      selectionReason: '입력으로 전달된 known_official_domains 기반 후보',
      rejectionReason: rejected ? 'reject_domains 규칙과 충돌' : undefined,
    });
  }

  if (typeof options.searchWeb === 'function' && state?.search_context?.allow_search_engine_queries !== false) {
    for (const query of searchQueries) {
      const results = (await options.searchWeb(query, { target, state })) || [];

      for (const result of results) {
        const domain = extractDomainFromUrl(result?.url);
        if (!domain) {
          continue;
        }

        const rejected = isRejectedDomain(domain, rejectDomains);
        pushDomainCandidate({
          domain,
          source: 'search_result',
          isBrandOwnedCandidate: domainLooksBrandOwned(domain, target) && !rejected,
          selectionReason: result?.title
            ? `검색 결과 제목: ${compactWhitespace(result.title).slice(0, 120)}`
            : '검색 결과에 노출된 도메인',
          rejectionReason: rejected ? 'reject_domains 규칙과 충돌' : undefined,
        });

        if (!rejected && result?.url) {
          pageSeeds.push(result.url);
        }
      }
    }
  }

  for (const heuristicDomain of buildHeuristicDomains(target, state?.geography)) {
    const rejected = isRejectedDomain(heuristicDomain, rejectDomains);
    pushDomainCandidate({
      domain: heuristicDomain,
      source: 'manual_hint',
      isBrandOwnedCandidate: domainLooksBrandOwned(heuristicDomain, target) && !rejected,
      selectionReason: '브랜드명 기반 휴리스틱 도메인 후보',
      rejectionReason: rejected ? 'reject_domains 규칙과 충돌' : undefined,
    });
  }

  const selectedDomainCandidates = candidateDomains
    .filter((candidate) => candidate.is_brand_owned_candidate && !candidate.rejection_reason)
    .slice(0, maxCandidates)
    .map((candidate) => candidate.domain);

  return {
    result: {
      target_id: target.target_id,
      brand: target.brand,
      product_name: target.product_name,
      search_queries: searchQueries,
      candidate_domains: candidateDomains,
      selected_domain_candidates: selectedDomainCandidates,
    },
    pageSeeds: uniq(pageSeeds),
  };
}

function buildPageCandidates(target, domains, pageSeeds, state) {
  const productSlugs = uniq([
    slugify(target.product_name),
    slugify(target.product_name.replace(target.brand, '').trim()),
  ]).filter(Boolean);
  const maxPerDomain =
    state?.search_context?.max_page_candidates_per_domain || DEFAULT_MAX_PAGE_CANDIDATES;
  const candidates = [];

  for (const seed of pageSeeds) {
    const seedUrl = safeUrl(seed);
    if (!seedUrl) {
      continue;
    }

    const normalizedDomain = normalizeHost(seedUrl.hostname);
    if (!domains.includes(normalizedDomain)) {
      continue;
    }

    candidates.push(seedUrl.toString());
  }

  for (const domain of domains) {
    const domainCandidates = [`https://${domain}/`];
    for (const slug of productSlugs) {
      domainCandidates.push(
        `https://${domain}/${slug}`,
        `https://${domain}/products/${slug}`,
        `https://${domain}/product/${slug}`,
        `https://${domain}/p/${slug}`,
        `https://${domain}/cards/${slug}`,
        `https://${domain}/card/${slug}`,
        `https://${domain}/help/${slug}`,
        `https://${domain}/faq/${slug}`
      );
    }

    candidates.push(...domainCandidates.slice(0, maxPerDomain + 2));
  }

  return uniq(candidates);
}

function decodeHtmlEntities(value) {
  return String(value || '')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}

function extractTitle(html) {
  const match = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  return match ? compactWhitespace(decodeHtmlEntities(match[1])) : '';
}

function extractCanonicalUrl(html, baseUrl) {
  const match = html.match(/<link[^>]+rel=["']canonical["'][^>]+href=["']([^"']+)["']/i);
  if (!match) {
    return '';
  }

  try {
    return new URL(match[1], baseUrl).toString();
  } catch {
    return '';
  }
}

function stripHtml(html) {
  return compactWhitespace(
    String(html || '')
      .replace(/<script[\s\S]*?<\/script>/gi, ' ')
      .replace(/<style[\s\S]*?<\/style>/gi, ' ')
      .replace(/<[^>]+>/g, ' ')
  );
}

function guessPageType(url, title, text) {
  const source = `${url} ${title} ${text}`.toLowerCase();
  if (/faq|자주 묻는|frequently asked/.test(source)) return 'faq_page';
  if (/help|support|고객지원|이용안내/.test(source)) return 'help_center_article';
  if (/price|pricing|fee|요금|수수료/.test(source)) return 'pricing_page';
  if (/product|card|service|상품|카드/.test(source) && !/\/$/.test(url)) return 'product_detail';
  if (/^https?:\/\/[^/]+\/?$/.test(url)) return 'brand_homepage';
  return 'other';
}

function matchSignals(text, tokens, label) {
  const lower = String(text || '').toLowerCase();
  return tokens
    .filter((token) => lower.includes(token.toLowerCase()))
    .map((token) => `${label}에 ${token} 포함`);
}

function classifyFetchStatus(response, candidateUrl) {
  if (!response.ok && response.status === 404) {
    return 'not_found';
  }

  if (!response.ok && response.status >= 400) {
    return 'error';
  }

  return response.finalUrl !== candidateUrl ? 'redirected' : 'ok';
}

async function fetchPage(url, options) {
  const fetchImpl = options.fetchImpl || global.fetch;
  if (typeof fetchImpl !== 'function') {
    throw new Error('fetch 구현이 필요합니다. options.fetchImpl 또는 Node.js 내장 fetch를 사용하세요.');
  }

  const controller = new AbortController();
  const timeoutMs = options.timeoutMs || DEFAULT_TIMEOUT_MS;
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetchImpl(url, {
      method: 'GET',
      redirect: 'follow',
      signal: controller.signal,
      headers: {
        'user-agent': FETCH_USER_AGENT,
        accept: 'text/html,application/xhtml+xml',
      },
    });

    const text = await response.text();
    return {
      ok: response.ok,
      status: response.status,
      finalUrl: response.url || url,
      html: text.slice(0, 300000),
    };
  } finally {
    clearTimeout(timeout);
  }
}

function scoreValidation(validation, options) {
  let score = 0;

  if (validation.status === 'ok') score += 0.35;
  if (validation.status === 'redirected') score += 0.25;
  if ((validation.http_status || 0) >= 200 && (validation.http_status || 0) < 300) score += 0.15;
  if (validation.candidate_url.startsWith('https://')) score += 0.05;
  if (validation.page_type_guess === 'product_detail') score += 0.2;
  if (validation.page_type_guess === 'pricing_page') score += 0.12;
  if (validation.page_type_guess === 'help_center_article') score += 0.1;
  if (validation.page_type_guess === 'faq_page') score += 0.08;
  if (validation.page_type_guess === 'brand_homepage') score += 0.06;

  score += Math.min(validation.brand_match_signals.length * 0.05, 0.15);
  score += Math.min(validation.product_match_signals.length * 0.06, 0.18);
  score += Math.min(validation.officiality_signals.length * 0.04, 0.16);
  score -= Math.min((validation.blocking_issues || []).length * 0.12, 0.36);

  const confidence = clampConfidence(score);
  const minVerified =
    options?.minConfidenceForVerified || DEFAULT_VERIFIED_CONFIDENCE;

  let verdict = 'ambiguous';
  if (validation.selection_decision === 'rejected') {
    verdict = 'rejected';
  } else if (
    confidence >= minVerified &&
    validation.product_match_signals.length > 0 &&
    validation.blocking_issues.length === 0
  ) {
    verdict = 'verified';
  } else if (confidence >= 0.6) {
    verdict = 'likely_official';
  }

  return { confidence, verdict };
}

async function validateTargetPages(target, discovery, state, options) {
  const candidateUrls = buildPageCandidates(
    target,
    discovery.result.selected_domain_candidates,
    discovery.pageSeeds,
    state
  );
  const validations = [];

  for (const candidateUrl of candidateUrls) {
    try {
      const response = await fetchPage(candidateUrl, options);
      const text = stripHtml(response.html);
      const pageTitle = extractTitle(response.html) || response.finalUrl;
      const canonicalUrl = state?.validation_preferences?.capture_canonical_url === false
        ? ''
        : extractCanonicalUrl(response.html, response.finalUrl);
      const brandMatches = matchSignals(
        `${pageTitle} ${text} ${response.finalUrl}`,
        brandTokens(target),
        '브랜드'
      );
      const productMatches = matchSignals(
        `${pageTitle} ${text} ${response.finalUrl}`,
        productTokens(target),
        '상품'
      );
      const finalDomain = normalizeHost(new URL(response.finalUrl).hostname);
      const officialitySignals = [];
      const blockingIssues = [];

      if (discovery.result.selected_domain_candidates.includes(finalDomain)) {
        officialitySignals.push('선정된 공식 도메인 후보와 일치');
      }
      if (canonicalUrl && normalizeHost(new URL(canonicalUrl).hostname) === finalDomain) {
        officialitySignals.push('canonical URL 도메인 일치');
      }
      if (pageTitle) {
        officialitySignals.push('페이지 제목 확보');
      }

      const pageTypeGuess = guessPageType(response.finalUrl, pageTitle, text.slice(0, 4000));
      if (!response.ok) {
        blockingIssues.push(`HTTP ${response.status}`);
      }
      if (brandMatches.length === 0) {
        blockingIssues.push('브랜드 매칭 신호 부족');
      }

      validations.push({
        validation_id: `val_${randomUUID()}`,
        target_id: target.target_id,
        candidate_url: candidateUrl,
        final_url: response.finalUrl,
        http_status: response.status,
        status: classifyFetchStatus(response, candidateUrl),
        page_title: pageTitle,
        ...(canonicalUrl ? { canonical_url: canonicalUrl } : {}),
        page_type_guess: pageTypeGuess,
        brand_match_signals: brandMatches,
        product_match_signals: productMatches,
        officiality_signals: officialitySignals,
        blocking_issues: blockingIssues,
        selection_decision: 'rejected',
      });
    } catch (error) {
      validations.push({
        validation_id: `val_${randomUUID()}`,
        target_id: target.target_id,
        candidate_url: candidateUrl,
        final_url: candidateUrl,
        status: error?.name === 'AbortError' ? 'blocked' : 'error',
        page_title: candidateUrl,
        page_type_guess: 'other',
        brand_match_signals: [],
        product_match_signals: [],
        officiality_signals: [],
        blocking_issues: [error.message || '페이지 검증 실패'],
        selection_decision: 'rejected',
      });
    }
  }

  const ranked = validations
    .map((validation) => {
      const { confidence } = scoreValidation(validation, {
        minConfidenceForVerified: state?.validation_preferences?.min_confidence_for_verified,
      });
      return { ...validation, _confidence: confidence };
    })
    .sort((a, b) => b._confidence - a._confidence);

  const maxSources = DEFAULT_MAX_SOURCES_PER_TARGET;
  const keepCount = state?.source_preferences?.max_sources_per_target || maxSources;
  const selectedIds = new Set(
    ranked
      .filter((validation) => validation._confidence >= 0.45)
      .slice(0, keepCount)
      .map((validation, index) => {
        validation.selection_decision = index === 0 ? 'selected' : 'fallback';
        return validation.validation_id;
      })
  );

  return validations.map((validation) => {
    if (!selectedIds.has(validation.validation_id)) {
      return validation;
    }

    const selected = ranked.find((item) => item.validation_id === validation.validation_id);
    return {
      ...validation,
      selection_decision: selected.selection_decision,
    };
  });
}

function mapPageTypeToSourceType(pageType) {
  switch (pageType) {
    case 'product_detail':
      return 'official_product_page';
    case 'help_center_article':
      return 'official_help_center';
    case 'pricing_page':
      return 'official_pricing_page';
    case 'faq_page':
      return 'official_faq';
    case 'brand_homepage':
    default:
      return 'official_site';
  }
}

function buildRationale(validation) {
  return uniq([
    ...validation.officiality_signals,
    ...validation.brand_match_signals,
    ...validation.product_match_signals,
  ])
    .slice(0, 4)
    .join(', ');
}

function buildSourceOutputs(target, validations, state) {
  const selected = validations.filter(
    (validation) => validation.selection_decision === 'selected' || validation.selection_decision === 'fallback'
  );

  if (selected.length === 0) {
    const bestAttempt = validations
      .map((validation) => ({
        ...validation,
        ...scoreValidation(validation, {
          minConfidenceForVerified: state?.validation_preferences?.min_confidence_for_verified,
        }),
      }))
      .sort((a, b) => b.confidence - a.confidence)[0];

    return {
      officialSources: [],
      sourceValidation: [],
      unresolvedTarget: {
        target_id: target.target_id,
        brand: target.brand,
        product_name: target.product_name,
        reason: bestAttempt
          ? `공식성 점수가 충분한 페이지를 찾지 못함 (${bestAttempt.status})`
          : '검증 가능한 페이지 후보를 찾지 못함',
        suggested_next_step: 'search adapter를 연결해 브랜드 공식 도메인 검색 결과를 추가 확보',
      },
    };
  }

  const officialSources = [];
  const sourceValidation = [];

  for (const validation of selected) {
    const { confidence, verdict } = scoreValidation(validation, {
      minConfidenceForVerified: state?.validation_preferences?.min_confidence_for_verified,
    });
    const sourceId = `src_${randomUUID()}`;
    const recommendedUse =
      validation.selection_decision === 'selected' ? 'primary_reference' : 'secondary_reference';
    const evidence = [];

    if (validation.http_status) {
      evidence.push(`HTTP ${validation.http_status}`);
    }
    if (validation.canonical_url) {
      evidence.push('canonical URL 확보');
    }
    if (validation.page_title) {
      evidence.push(`title: ${validation.page_title.slice(0, 80)}`);
    }
    evidence.push(...validation.brand_match_signals.slice(0, 2));
    evidence.push(...validation.product_match_signals.slice(0, 2));

    officialSources.push({
      source_id: sourceId,
      target_id: target.target_id,
      target_type: target.target_type,
      brand: target.brand,
      product_name: target.product_name,
      source_type: mapPageTypeToSourceType(validation.page_type_guess),
      url: validation.final_url,
      domain: normalizeHost(new URL(validation.final_url).hostname),
      page_title: validation.page_title,
      selected_from_validation_id: validation.validation_id,
      rationale: buildRationale(validation) || '실제 페이지 검증 기준 최소 조건 충족',
      confidence,
      needs_validation: verdict !== 'verified',
    });

    sourceValidation.push({
      source_id: sourceId,
      target_id: target.target_id,
      url: validation.final_url,
      verdict,
      positive_signals: uniq([
        ...validation.officiality_signals,
        ...validation.brand_match_signals,
        ...validation.product_match_signals,
      ]),
      negative_signals: validation.blocking_issues,
      recommended_use: verdict === 'rejected' ? 'do_not_use' : recommendedUse,
      validation_evidence: uniq(evidence),
      notes:
        verdict === 'verified'
          ? '후속 공식 정보 추출의 기준 URL로 사용 가능'
          : '추가 검색 또는 사람 검토와 함께 사용하는 것이 안전함',
    });
  }

  return {
    officialSources,
    sourceValidation,
    unresolvedTarget: null,
  };
}

async function officialSourceResolverNode(state, options = {}) {
  const runId = state?.run_id || `run_${Date.now()}`;
  const createdAt = isoNow();
  const startedAt = createdAt;
  const targets = buildResolutionTargets(state);

  if (!state?.project_id) {
    throw new Error('official_source_resolver_node requires state.project_id');
  }

  if (targets.length === 0) {
    throw new Error('official_source_resolver_node requires own_product or competitor_candidates');
  }

  const agentSteps = [];
  const domainDiscoveryResults = [];
  const pageValidationResults = [];
  const officialSources = [];
  const sourceValidation = [];
  const unresolvedTargets = [];

  try {
    const domainDiscoveryStartedAt = isoNow();
    const discoveries = [];

    for (const target of targets) {
      const discovery = await discoverDomainsForTarget(target, state, options);
      domainDiscoveryResults.push(discovery.result);
      discoveries.push({ target, discovery });
    }

    agentSteps.push(
      buildStep(
        'OfficialSourceResolverAgent.brand_official_domain_discovery',
        'completed',
        domainDiscoveryStartedAt,
        isoNow()
      )
    );

    const pageValidationStartedAt = isoNow();

    for (const item of discoveries) {
      const validations = await validateTargetPages(item.target, item.discovery, state, options);
      pageValidationResults.push(...validations);

      const sourceOutputs = buildSourceOutputs(item.target, validations, state);
      officialSources.push(...sourceOutputs.officialSources);
      sourceValidation.push(...sourceOutputs.sourceValidation);

      if (sourceOutputs.unresolvedTarget) {
        unresolvedTargets.push(sourceOutputs.unresolvedTarget);
      }
    }

    agentSteps.push(
      buildStep(
        'OfficialSourceResolverAgent.official_page_validation',
        'completed',
        pageValidationStartedAt,
        isoNow()
      )
    );

    agentSteps.push(
      buildStep('OfficialSourceResolverAgent', 'completed', startedAt, isoNow())
    );

    return {
      run_id: runId,
      project_id: state.project_id,
      domain_discovery_results: domainDiscoveryResults,
      page_validation_results: pageValidationResults,
      resolution_targets: targets,
      official_sources: officialSources,
      source_validation: sourceValidation,
      unresolved_targets: unresolvedTargets,
      created_at: createdAt,
      agent_steps: agentSteps,
    };
  } catch (error) {
    const failedUpdate = {
      run_id: runId,
      project_id: state.project_id,
      domain_discovery_results: domainDiscoveryResults,
      page_validation_results: pageValidationResults,
      resolution_targets: targets,
      official_sources: officialSources,
      source_validation: sourceValidation,
      unresolved_targets: unresolvedTargets,
      created_at: createdAt,
      agent_steps: [
        ...agentSteps,
        buildStep('OfficialSourceResolverAgent', 'failed', startedAt, isoNow(), error.message),
      ],
      errors: [error.message],
    };

    throw new OfficialSourceResolverNodeError(
      error.message || 'official source resolver node failed',
      failedUpdate,
      error
    );
  }
}

async function persistOfficialSourceResolverArtifacts(projectRoot, update) {
  const sourcesDir = path.join(projectRoot, 'sources');

  await writeJsonFile(path.join(sourcesDir, 'domain_discovery_results.json'), {
    project_id: update.project_id,
    resolved_at: update.created_at,
    domain_discovery_results: update.domain_discovery_results,
  });

  await writeJsonFile(path.join(sourcesDir, 'page_validation_results.json'), {
    project_id: update.project_id,
    validated_at: update.created_at,
    page_validation_results: update.page_validation_results,
  });

  await writeJsonFile(path.join(sourcesDir, 'official_sources.json'), {
    project_id: update.project_id,
    resolved_at: update.created_at,
    official_sources: update.official_sources,
  });

  await writeJsonFile(path.join(sourcesDir, 'source_validation.json'), {
    project_id: update.project_id,
    validated_at: update.created_at,
    source_validation: update.source_validation,
    unresolved_targets: update.unresolved_targets,
  });
}

module.exports = {
  OfficialSourceResolverNodeError,
  officialSourceResolverNode,
  persistOfficialSourceResolverArtifacts,
};
