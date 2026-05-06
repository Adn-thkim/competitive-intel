const fs = require('node:fs/promises');
const path = require('node:path');

async function ensureDir(dirPath) {
  await fs.mkdir(dirPath, { recursive: true });
}

async function writeJsonFile(filePath, value) {
  await ensureDir(path.dirname(filePath));
  await fs.writeFile(filePath, JSON.stringify(value, null, 2), 'utf8');
}

async function readJsonFile(filePath) {
  const content = await fs.readFile(filePath, 'utf8');
  return JSON.parse(content);
}

module.exports = {
  ensureDir,
  readJsonFile,
  writeJsonFile,
};
