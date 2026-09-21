/**
 * 在 Node 里跑一遍 web/index.html 的脚本，检查「文件夹导入 + 进度提示」的界面逻辑。
 * 输出 JSON 结果，由 tests/test_web_ui.py 断言。
 */
const fs = require("fs");
const path = require("path");
const { installDom, flushTimers, flushMicrotasks } = require("./dom_stub.js");

const htmlPath = path.join(__dirname, "..", "..", "web", "index.html");
const html = fs.readFileSync(htmlPath, "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
if (!scripts.length) throw new Error("web/index.html 里没有找到脚本");

const scenario = process.argv[2] || "import";
const dom = installDom();

let people = [
  { name: "伍雅芳", student_id: "1", semester: "2026-2027学年第1学期", group: "", courses: [] },
  { name: "闫爱琳", student_id: "2", semester: "2026-2027学年第1学期", group: "", courses: [] },
];
const calls = [];
const statusQueue = [
  { running: true, finished: false, total: 3, done: 1, current: "a.pdf", added: 0, updated: 0, errors: [], folder: "D:\\课表" },
  { running: true, finished: false, total: 3, done: 2, current: "b.pdf", added: 1, updated: 0, errors: [], folder: "D:\\课表" },
  {
    running: false,
    finished: true,
    total: 3,
    done: 3,
    current: "",
    added: 2,
    updated: 1,
    errors: [{ path: "D:\\课表\\坏文件.pdf", error: "未识别到课表内容" }],
    folder: "D:\\课表",
  },
];

const api = {
  get_people: async () => {
    calls.push("get_people");
    return people;
  },
  get_groups: async () => {
    calls.push("get_groups");
    return [];
  },
  get_app_info: async () => {
    calls.push("get_app_info");
    return { name: "多人空课表", version: "2.1", developer: "阿山", contact: "", copyright: "© 2026 阿山", needs_refresh: false };
  },
  start_import: async (kind, group) => {
    calls.push(`start_import:${kind}:${group}`);
    return { started: true, total: 3, folder: "D:\\课表" };
  },
  get_import_status: async () => {
    calls.push("get_import_status");
    return statusQueue.length ? statusQueue.shift() : { running: false, finished: true, total: 3, done: 3, added: 2, updated: 1, errors: [], current: "", folder: "" };
  },
  remove_people: async (indices) => {
    calls.push(`remove_people:${(indices || []).join(",")}`);
    people = [];
    return { people, groups: [] };
  },
};

globalThis.window.pywebview = { api };

(async () => {
  // 执行界面脚本
  new Function(scripts[scripts.length - 1])();
  await flushTimers(3);

  const result = { steps: [], calls };

  if (scenario === "delete") {
    const removeBtn = dom.document.getElementById("people-remove-btn");
    const confirmModal = dom.document.getElementById("confirm-modal");
    const confirmMessage = dom.document.getElementById("confirm-message");
    const confirmOk = dom.document.getElementById("confirm-ok");
    const toastEl = dom.document.getElementById("toast");
    const checkedCount = dom.document.getElementById("checked-count");

    result.steps.push({ name: "初始勾选提示", checkedCountText: checkedCount.textContent });

    removeBtn.dispatch("click", {});
    await flushMicrotasks();
    result.steps.push({
      name: "点击删除",
      confirmShown: confirmModal.hidden === false,
      confirmText: confirmMessage.textContent,
    });

    confirmOk.dispatch("click", {});
    await flushTimers(3);
    result.steps.push({
      name: "确认删除",
      confirmHidden: confirmModal.hidden === true,
      toastText: toastEl.textContent,
    });

    process.stdout.write(JSON.stringify(result));
    return;
  }

  const progressEl = dom.document.getElementById("progress");
  const fillEl = dom.document.getElementById("progress-fill");
  const countEl = dom.document.getElementById("progress-count");
  const noteEl = dom.document.getElementById("progress-note");
  const toastEl = dom.document.getElementById("toast");
  const groupInput = dom.document.getElementById("import-group-input");
  const folderBtn = dom.document.getElementById("import-folder");

  // 1) 打开导入弹窗 -> 点「选择文件夹并导入」
  groupInput.value = "记者部";
  folderBtn.dispatch("click", {});
  await flushMicrotasks();
  result.steps.push({
    name: "导入开始（1/3）",
    progressVisible: progressEl.hidden === false,
    countText: countEl.textContent,
    fillWidth: fillEl.style.width,
    noteText: noteEl.textContent,
  });

  // 2) 下一次轮询：2/3，进度条 67%
  await flushTimers(1);
  result.steps.push({
    name: "进度中（2/3）",
    progressVisible: progressEl.hidden === false,
    countText: countEl.textContent,
    fillWidth: fillEl.style.width,
    noteText: noteEl.textContent,
  });

  // 3) 最后一次轮询：完成，进度条隐藏，弹出汇总（含失败文件）
  await flushTimers(2);
  result.steps.push({
    name: "导入完成",
    progressHidden: progressEl.hidden === true,
    toastText: toastEl.textContent,
    peopleReloaded: calls.filter((c) => c === "get_people").length,
  });

  process.stdout.write(JSON.stringify(result));
})().catch((error) => {
  process.stdout.write(JSON.stringify({ error: String(error && error.stack ? error.stack : error) }));
  process.exitCode = 1;
});


