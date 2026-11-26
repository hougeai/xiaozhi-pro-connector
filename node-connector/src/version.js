import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// 版本号唯一来源：package.json。禁止在其他文件硬编码，避免发布后 --version 对不上。
export const VERSION = JSON.parse(
  readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'package.json'), 'utf8'),
).version;
