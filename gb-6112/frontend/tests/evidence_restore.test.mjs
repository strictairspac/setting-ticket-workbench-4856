// 改号交接页恢复流程的集成测试：真实加载页面模块，模拟 API 与用户交互。

import assert from "node:assert/strict";
import { installDom, installFetch, findByTestId, findByTag } from "./dom_harness.mjs";

installDom();
const { evidencePage } = await import("../src/pages/evidence.js");

const CONFIG = {
  modules: ["转版审阅", "旧票卷宗", "目标试写", "改号交接"],
  parameter_label: "本次允许改号张数", fixture: "x.json",
};
const OPERATOR = {username: "operator", role: "operator"};

const RESULT = {
  summary: "2 组同间冲突，改号 2 张后全卷通过目标唯一约束。",
  metrics: {"冲突组": 2, "需改号": 2, "通过试写": 7},
  rows: [
    {"工票 ID": "N001", "工作间": "北间", "原票号": "ÉCLAT-27", "目标票号": "ÉCLAT-27", "动作": "保留"},
    {"工票 ID": "N002", "工作间": "北间", "原票号": "eclat-27", "目标票号": "R-N002-2", "动作": "别名已验"},
  ],
};
const REVIEW = {run_id: 1, actor: "reviewer", note: "核对无误", created_at: 300};

function state() {
  return {
    runs: [
      {id: 1, case_id: 1, case_title: "北间冬展", status: "done", result: null, digest: "D1", failure: null},
      {id: 2, case_id: 1, case_title: "西间批次", status: "done", result: RESULT, digest: "D2", failure: null},
    ],
    reviews: {1: null, 2: REVIEW},
    repairs: {},
    nextRepairId: 1,
  };
}

function routerFor(store, failOnce) {
  return ({method, path, body}) => {
    if (path === "/runs" && method === "GET") return {json: store.runs};
    const runMatch = path.match(/^\/runs\/(\d+)$/);
    if (runMatch && method === "GET") return {json: store.runs.find(r => r.id === Number(runMatch[1]))};
    const reviewMatch = path.match(/^\/runs\/(\d+)\/review$/);
    if (reviewMatch && method === "GET") return {json: store.reviews[Number(reviewMatch[1])] ?? null};
    const repairMatch = path.match(/^\/runs\/(\d+)\/repair$/);
    if (repairMatch && method === "GET") return {json: store.repairs[Number(repairMatch[1])] ?? null};
    const markMatch = path.match(/^\/runs\/(\d+)\/repair\/mark$/);
    if (markMatch && method === "POST") {
      const run = store.runs.find(r => r.id === Number(markMatch[1]));
      run.status = "repairing";
      return {json: null};
    }
    const restoreMatch = path.match(/^\/runs\/(\d+)\/repair\/restore$/);
    if (restoreMatch && method === "POST") {
      const runId = Number(restoreMatch[1]);
      const run = store.runs.find(r => r.id === runId);
      if (failOnce.value) {
        failOnce.value = false;
        return {error: "备份校验值与库内留存的校验值不一致，不能恢复", status: 422};
      }
      if (body.backup.id !== runId) return {error: "批次号不一致", status: 422};
      run.status = "done";
      run.result = body.backup.result;
      run.digest = "D1";
      store.repairs[runId] = {
        run_id: runId, actor: "operator", source: body.source, digest: "D1", created_at: 400,
      };
      return {json: store.repairs[runId]};
    }
    const exportMatch = path.match(/^\/runs\/(\d+)\/export$/);
    if (exportMatch && method === "GET") {
      const run = store.runs.find(r => r.id === Number(exportMatch[1]));
      if (run.status !== "done" || !run.result) return {error: "批次待修复，暂时无法导出交接单", status: 409};
      return {json: {...run, review: store.reviews[run.id] ?? null, repair: store.repairs[run.id] ?? null}};
    }
    throw new Error(`未模拟的接口 ${method} ${path}`);
  };
}

function flush() {
  return new Promise(resolve => setTimeout(resolve, 0));
}

async function pageFor(store, route, user = OPERATOR, failOnce = {value: false}) {
  const calls = installFetch(routerFor(store, failOnce));
  const root = await evidencePage(CONFIG, user, route);
  await flush();
  return {root, calls};
}

// 场景 1：结果缺失的已完成批次，操作员标记为待修复。
async function test_mark_damaged() {
  const store = state();
  const {root} = await pageFor(store, {id: 1});
  const button = findByTestId(root, "mark-damaged");
  assert.ok(button, "结果缺失批次应显示标记按钮");
  let hashChanged = 0;
  window.addEventListener("hashchange", () => { hashChanged += 1; });
  button.click();
  await flush();
  assert.equal(store.runs[0].status, "repairing");
  assert.equal(hashChanged, 1, "标记后应触发路由刷新");
}

// 场景 2：待修复批次不展示业务明细，也没有导出按钮（其他批次照常可查）。
async function test_repairing_hides_result() {
  const store = state();
  store.runs[0].status = "repairing";
  const {root} = await pageFor(store, {id: 1});
  assert.ok(findByTestId(root, "repairing-notice"), "应显示待修复提示");
  const tables = findByTag(root, "table");
  assert.equal(tables.length, 0, "待修复期间不渲染结果明细表");
  const buttons = findByTag(root, "button").map(b => b.textContent);
  assert.ok(!buttons.includes("导出证据"), "待修复期间不能导出");
  // 同页批次下拉中其他已完成批次仍可选择。
  const options = findByTag(root, "option").map(o => o.textContent);
  assert.ok(options.some(text => text.includes("西间批次")));
}

// 场景 3：上传备份恢复，先失败（留提示、不改状态），重试成功后可查阅与导出，修复记录留痕。
async function test_restore_then_view_and_export() {
  const store = state();
  store.runs[0].status = "repairing";
  const failOnce = {value: true};
  const {root, calls} = await pageFor(store, {id: 1}, OPERATOR, failOnce);

  const form = root.querySelector("#restore-form");
  assert.ok(form, "待修复批次应提供恢复表单");
  const input = form.elements.backup;
  input.files = [{name: "evidence-1.json", content: JSON.stringify({id: 1, result: RESULT, digest: "D1"})}];
  form.dispatch("submit");
  await flush();
  assert.equal(store.runs[0].status, "repairing", "核验失败时状态不变");
  assert.equal(store.runs[0].result, null);
  assert.ok(!store.repairs[1], "失败尝试不写修复记录");

  // 重试：同一份备份，这次核验通过（failOnce 已耗尽）。
  form.dispatch("submit");
  await flush();
  assert.equal(store.runs[0].status, "done");
  assert.deepEqual(store.runs[0].result, RESULT);
  assert.ok(store.repairs[1], "恢复成功后写入修复记录");
  assert.equal(store.repairs[1].source, "evidence-1.json");

  // 重新渲染：完整原单可查阅，原签署区仍在，修复记录可见，并能导出。
  const restored = await pageFor(store, {id: 1});
  assert.ok(findByTestId(restored.root, "repaired"), "应展示修复记录");
  assert.equal(findByTag(restored.root, "table").length, 1, "恢复后展示业务结果明细表");
  const exportButton = findByTag(restored.root, "button").find(b => b.textContent === "导出证据");
  assert.ok(exportButton, "恢复后可导出完整原单");
  exportButton.click();
  await flush();
  assert.ok(restored.calls.some(c => c.path === "/runs/1/export"), "导出请求已发出");}

// 场景 4：备份缺签署也能恢复，且不会改动库内原签署。
async function test_backup_without_review_keeps_existing() {
  const store = state();
  store.runs[0].status = "repairing";
  store.reviews[1] = REVIEW;  // 库里原签署保留
  const {root} = await pageFor(store, {id: 1});
  const form = root.querySelector("#restore-form");
  form.elements.backup.files = [{
    name: "no-review.json",
    content: JSON.stringify({id: 1, result: RESULT, digest: "D1"}),  // 无 review 字段
  }];
  form.dispatch("submit");
  await flush();
  assert.equal(store.runs[0].status, "done");
  assert.deepEqual(store.reviews[1], REVIEW, "原签署照旧保留");
}

// 场景 5：非操作员看不到标记/恢复入口。
async function test_viewer_is_readonly() {
  const store = state();
  const {root} = await pageFor(store, {id: 1}, {username: "viewer", role: "viewer"});
  assert.equal(findByTestId(root, "mark-damaged"), null);
  store.runs[0].status = "repairing";
  const repairing = await pageFor(store, {id: 1}, {username: "viewer", role: "viewer"});
  assert.equal(repairing.root.querySelector("#restore-form"), null);
}

const tests = [
  test_mark_damaged,
  test_repairing_hides_result,
  test_restore_then_view_and_export,
  test_backup_without_review_keeps_existing,
  test_viewer_is_readonly,
];
for (const test of tests) {
  await test();
  console.log(`PASS ${test.name}`);
}
console.log(`\n${tests.length} 个前端集成用例全部通过`);
