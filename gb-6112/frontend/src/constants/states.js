export const States = Object.freeze({
  queued: "等待执行", done: "已完成", failed: "执行失败", repairing: "待修复",
});

export const Actions = Object.freeze({
  create_case: "登记资料", enqueue: "提交执行", done: "执行完成", failed: "执行失败",
  review: "复核签署", mark_repair: "标记待修复", restore: "恢复改号结果",
});
