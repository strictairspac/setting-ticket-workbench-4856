import { api } from "../api/workbench.js";
import { errorText, field } from "../components/common/fields.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { status } from "../components/common/status.js";
import { resultTable } from "../components/common/table.js";
import { submit } from "../hooks/submit.js";
import { el } from "../utils/dom.js";
import { cell, download, time } from "../utils/format.js";

function reviewForm(id, saved) {
  const form = el("form", {id: "review-form"},
    field("复核意见", el("textarea", {name: "note", required: true, minlength: 3, maxlength: 500, rows: 3})),
    errorText(), el("button", {type: "submit"}, "签署复核"));
  submit(form, async values => saved(await api.submitReview(id, {note: values.get("note")})));
  return form;
}

function signature(review) {
  return el("div", {"data-testid": "reviewed", class: "signature"},
    el("strong", {}, `已签署 · ${review.actor}`), el("p", {}, review.note), el("time", {}, time(review.created_at)));
}

function repairRecord(record) {
  return el("div", {"data-testid": "repaired", class: "signature repair-record"},
    el("strong", {}, `已恢复 · ${record.actor}`),
    el("p", {}, `来源：${record.source}`),
    el("time", {}, time(record.created_at)),
    el("details", {}, el("summary", {}, "恢复校验值"), el("code", {}, record.digest)));
}

function readBackupFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        resolve(JSON.parse(reader.result));
      } catch {
        reject(new Error("备份文件不是有效的 JSON，请选择导出的交接单"));
      }
    };
    reader.onerror = () => reject(new Error("备份文件读取失败"));
    reader.readAsText(file);
  });
}

function restoreForm(runId, issue, onDone) {
  const form = el("form", {id: "restore-form"},
    field("交接单备份", el("input", {type: "file", name: "backup", accept: "application/json,.json", required: true}),
      "选择此前下载的 evidence 备份；备份缺少签署也可使用，原签署照旧保留。"),
    errorText(), el("button", {type: "submit"}, "核验并恢复"));
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const feedback = form.querySelector("[role=alert]");
    const button = form.querySelector("button[type=submit]");
    feedback.textContent = "";
    issue.textContent = "";
    const file = form.elements.backup.files[0];
    if (!file) {
      feedback.textContent = "请先选择备份文件";
      return;
    }
    button.disabled = true;
    try {
      const backup = await readBackupFile(file);
      await api.restoreRun(runId, backup, file.name);
      onDone();
    } catch (error) {
      feedback.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  return form;
}

function repairingView(run, user, reload) {
  const issue = errorText();
  const canRepair = user.role === "operator";
  return el("div", {class: "stack"},
    status(run.status),
    el("p", {role: "alert", "data-testid": "repairing-notice"},
      "该批次改号结果已丢失，修复完成前不能查看业务明细或下载交接单；其他批次可正常查阅。"),
    canRepair ? restoreForm(run.id, issue, reload) : empty("等待资料操作员上传备份恢复。"));
}

function damagedNotice(run, user, reload) {
  if (user.role !== "operator") return null;
  const issueLine = errorText();
  const button = el("button", {type: "button", "data-testid": "mark-damaged"}, "标记为待修复");
  button.addEventListener("click", async () => {
    button.disabled = true;
    issueLine.textContent = "";
    try {
      await api.markRepair(run.id);
      reload();
    } catch (error) {
      issueLine.textContent = error.message;
      button.disabled = false;
    }
  });
  return el("div", {class: "repair-banner"},
    el("p", {}, "该批次改号结果缺失但库内校验值仍在，可标记为待修复后用备份恢复。"),
    button, issueLine);
}

async function doneView(run, user, reload) {
  const [review, repair] = await Promise.all([api.review(run.id), api.repair(run.id)]);
  const issue = errorText();
  const button = el("button", {type: "button", onclick: async () => {
    button.disabled = true;
    issue.textContent = "";
    try {
      await download(await api.export(run.id), `evidence-${run.id}.json`);
    } catch (error) {
      issue.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }}, "导出证据");
  const signing = el("div", {});
  signing.append(review ? signature(review) : user.role === "reviewer" ?
    reviewForm(run.id, data => signing.replaceChildren(signature(data))) : empty("待复核员签署。"));
  return [
    repair ? repairRecord(repair) : null,
    el("section", {class: "feature evidence-summary"},
      el("div", {}, el("h2", {}, "批次判定"), el("p", {}, run.result.summary)),
      el("details", {}, el("summary", {}, "证据摘要"), el("code", {}, run.digest))),
    el("dl", {class: "metrics"}, Object.entries(run.result.metrics).map(([label, value]) =>
      el("div", {}, el("dt", {}, label), el("dd", {}, cell(value))))),
    section("业务结果明细", run.result.rows.length ? resultTable(run.result.rows) : empty("此批次无明细。")),
    section("复核签署", signing),
    issue,
    button,
  ];
}

export async function evidencePage(config, user, route) {
  const allRuns = await api.runs();
  const selectable = allRuns.filter(item => item.status === "done" || item.status === "repairing");
  const run = route.id ? await api.run(route.id) : selectable[0];

  const reload = () => {
    // 主动通知工作台按当前路由重新拉取并渲染，不依赖 hash 是否真的发生变化。
    window.dispatchEvent(new Event("hashchange"));
  };

  if (!run || (run.status !== "done" && run.status !== "repairing")) {
    return el("div", {class: "page"},
      pageHeading(config.modules[3], null), empty("成功执行一个批次后可以查看、签核和下载证据。"),
      el("a", {href: "#runs"}, config.modules[2]));
  }

  const options = el("select", {"aria-label": "选择已完成或待修复的批次", onchange: event => {
    location.hash = `#evidence/${event.target.value}`;
  }}, selectable.map(item => el("option", {value: item.id, selected: item.id === run.id},
    `#${item.id} ${item.case_title}（${item.status === "repairing" ? "待修复" : "已完成"}）`)));

  const body = run.status === "repairing"
    ? repairingView(run, user, reload)
    : run.result
      ? el("div", {class: "stack"}, ...(await doneView(run, user, reload)))
      : el("div", {class: "stack"},
        damagedNotice(run, user, reload) ?? empty("该批次改号结果缺失，等待标记为待修复。"));

  return el("div", {class: "page evidence"},
    pageHeading(config.modules[3], run.case_title),
    options, body);
}
