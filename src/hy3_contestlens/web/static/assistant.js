/* Markdown uses DOM text nodes, never model-supplied HTML. History stays raw. */
document.querySelectorAll('[data-assistant-run]').forEach(panel => {
  const form = panel.querySelector('form');
  const input = form.elements.question;
  const log = panel.querySelector('.assistant-messages');
  const status = panel.querySelector('.assistant-status');
  let history = [];
  let busy = false;
  const labels = {
    get_run_overview: '运行摘要', get_evaluations: '版本评测', get_test_results: '测试点结果',
    get_reviews: '评审与诊断', compare_revisions: '版本比较', get_run_events: '运行事件',
    get_failure_diagnostics: '失败诊断', get_environment_status: '当前环境检查'
  };
  function addMessage(role, text, evidence = []) {
    const box = document.createElement('article');
    box.className = `assistant-message assistant-${role}`;
    const heading = document.createElement('strong');
    heading.textContent = role === 'user' ? '你' : '评测助手';
    const references = new Map();
    const evidenceNodes = evidence.map(item => {
      const details = document.createElement('details');
      const title = document.createElement('summary');
      title.textContent = `[${item.evidence_id}] ${labels[item.tool] || item.tool}`;
      const body = document.createElement('pre');
      body.textContent = JSON.stringify({查询: item.arguments, 结果: item.data}, null, 2);
      details.append(title, body);
      references.set(item.evidence_id, details);
      return details;
    });
    const content = role === 'assistant' && window.renderAssistantMarkdown
      ? window.renderAssistantMarkdown(text, references) : document.createElement('p');
    if (!content.classList.contains('assistant-markdown')) content.textContent = text;
    box.append(heading, content);
    box.append(...evidenceNodes);
    log.append(box);
    return box;
  }
  function setBusy(value) {
    busy = value;
    panel.setAttribute('aria-busy', String(value));
    panel.querySelectorAll('button').forEach(button => button.disabled = value);
    input.disabled = value;
  }
  panel.querySelectorAll('[data-question]').forEach(button => {
    button.addEventListener('click', () => { input.value = button.dataset.question; input.focus(); });
  });
  panel.querySelector('.assistant-clear').addEventListener('click', () => {
    history = []; log.replaceChildren(); status.textContent = ''; input.value = ''; input.focus();
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const question = input.value.trim();
    if (busy || !question) return;
    setBusy(true);
    status.textContent = '正在查询证据并生成回答…';
    addMessage('user', question);
    try {
      const response = await fetch(`/api/v1/runs/${encodeURIComponent(panel.dataset.assistantRun)}/assistant`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({question, history: history.slice(-8)})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.message === 'string' ? data.message : '请求未完成，请稍后重试。');
      addMessage('assistant', data.answer, data.evidence || []);
      if (data.status === 'answered') {
        history.push({role: 'user', content: question}, {role: 'assistant', content: data.answer.slice(0, 8000)});
        history = history.slice(-8);
        input.value = '';
        status.textContent = '回答完成，可以继续追问。';
      } else {
        status.textContent = data.status === 'limited' ? '查询达到上限，可缩小问题范围。' : '模型暂不可用，可查看本地证据后重试。';
      }
    } catch (error) {
      addMessage('assistant', error.message || '网络连接失败，请重试。');
      status.textContent = '请求未完成，问题已保留。';
    } finally {
      setBusy(false); input.focus();
    }
  });
});
