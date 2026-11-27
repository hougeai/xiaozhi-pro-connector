import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline/promises';

import { log } from './log.js';

// 配置文件统一放用户 home 下的 .xiaozhi-pro，三个平台路径规则一致，便于用户在文档里一眼找到：
//   Linux   /home/<用户名>/.xiaozhi-pro/connector.json
//   macOS   /Users/<用户名>/.xiaozhi-pro/connector.json
//   Windows C:\Users\<用户名>\.xiaozhi-pro\connector.json
const CONFIG_DIR = path.join(os.homedir(), '.xiaozhi-pro');
export const CONFIG_PATH = path.join(CONFIG_DIR, 'connector.json');

export function loadConfig() {
  try {
    const data = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
    return Array.isArray(data.agents) ? data.agents : null;
  } catch {
    return null;
  }
}

export function saveConfig(agents) {
  fs.mkdirSync(CONFIG_DIR, { recursive: true });
  fs.writeFileSync(CONFIG_PATH, `${JSON.stringify({ agents }, null, 2)}\n`, 'utf8');
  log(`已保存 Agent 选择到 ${CONFIG_PATH}`);
}

function interactive() {
  return Boolean(process.stdin.isTTY && process.stdout.isTTY);
}

async function promptTargets(targets) {
  console.log('\n检测到以下 Agent，选择要接入小智的（输入编号，多个用逗号分隔，直接回车全选）：\n');
  // 括号里给 agent id，与 --agents 的取值一致；启动路径在探测日志里已经打过，这里不重复
  targets.forEach((target, index) => {
    console.log(`  ${index + 1}. ${target.name}  (${target.agent_id})`);
  });
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  try {
    const answer = (await rl.question('\n> ')).trim();
    if (!answer) return targets.map((target) => target.agent_id);
    // 提示只教逗号，解析顺带吃全角逗号与空格，避免输入法差异被判成无效输入
    const picked = answer
      .split(/[,，\s]+/)
      .filter(Boolean)
      .map(Number)
      .filter((n) => Number.isInteger(n) && n >= 1 && n <= targets.length)
      .map((n) => targets[n - 1].agent_id);
    return [...new Set(picked)];
  } finally {
    rl.close();
  }
}

function announce(chosen, source) {
  // 空选择由调用方报错退出，这里不再打一条「接入 0 个」的噪音日志
  if (!chosen.length) return;
  log(`${source ? `${source}，` : ''}本次接入 ${chosen.length} 个 Agent: ${chosen.map((target) => target.name).join(', ')}`);
}

function byIds(targets, ids, source) {
  const known = new Set(targets.map((target) => target.agent_id));
  const unknown = ids.filter((id) => !known.has(id));
  if (unknown.length) {
    log(`${source}中的 ${unknown.join(', ')} 本机未检测到，已跳过`);
  }
  return targets.filter((target) => ids.includes(target.agent_id));
}

/**
 * 决定本次启动接入哪些 Agent，优先级：
 *   --agents 指定的 > 配置文件记录的 > 交互式选择（无终端时直接报错）
 * --reset 清空已保存的选择并重新交互选择，新结果覆盖写回配置。
 */
export async function selectTargets(targets, opts) {
  if (opts.agents) {
    const chosen = byIds(targets, opts.agents.split(/[,，]/).map((x) => x.trim()).filter(Boolean), '--agents');
    if (opts.save) saveConfig(chosen.map((target) => target.agent_id));
    announce(chosen, '按 --agents');
    return chosen;
  }

  let resetHint = false;
  if (opts.reset) {
    resetHint = fs.existsSync(CONFIG_PATH);
  } else {
    const configured = loadConfig();
    if (configured) {
      const chosen = byIds(targets, configured, CONFIG_PATH);
      if (!chosen.length) {
        log(`${CONFIG_PATH} 里记录的 Agent 本机都没检测到，重新选择`);
      } else {
        announce(chosen, `按 ${CONFIG_PATH}`);
        return chosen;
      }
    }
  }

  if (!interactive()) {
    const reason = fs.existsSync(CONFIG_PATH)
      ? `${CONFIG_PATH} 已存在，但当前不是交互终端，无法交互选择`
      : `未找到 ${CONFIG_PATH}，且当前不是交互终端（nohup / CI 场景）`;
    throw new Error(
      `${reason}。\n请先在终端前台运行一次 xiaozhi-pro-connector 完成选择，或用 --agents claude,codex 指定。`,
    );
  }

  if (resetHint) log('已清空之前的选择');
  const ids = await promptTargets(targets);
  if (!ids.length) {
    throw new Error('没有选择任何 Agent，启动结束');
  }
  const chosen = targets.filter((target) => ids.includes(target.agent_id));
  saveConfig(ids);
  announce(chosen);
  return chosen;
}
