/* Small, deliberately HTML-free Markdown renderer for assistant answers.
 * All model-controlled strings become text nodes. No HTML parser, images or scripts.
 * Supports paragraphs, headings, nested lists, quotes, fenced code and simple tables.
 */
(() => {
  const make = (tag, text) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  function inline(parent, text, references, depth = 0) {
    if (depth > 12) { parent.append(document.createTextNode(text)); return; }
    const token = /(`+)([^\n]*?)\1(?!`)|\*\*([^\n]+?)\*\*|__([^\n]+?)__|\*([^*\n]+?)\*|~~([^\n]+?)~~|\[([^\]\n]+)\]\(([^\s)]+)\)|\[(E\d+)\]|\\([\\`*_[\]{}()#+.!|>~-])/g;
    let offset = 0;
    for (const match of text.matchAll(token)) {
      parent.append(document.createTextNode(text.slice(offset, match.index)));
      if (match[1]) {
        parent.append(make('code', match[2]));
      } else if (match[3] || match[4] || match[5] || match[6]) {
        const node = make(match[6] ? 'del' : match[5] ? 'em' : 'strong');
        inline(node, match[3] || match[4] || match[5] || match[6], references, depth + 1);
        parent.append(node);
      } else if (match[7]) {
        // Links remain readable but noninteractive: tool output cannot cause navigation.
        inline(parent, match[7], references, depth + 1);
        parent.append(document.createTextNode(` (${match[8]})`));
      } else if (match[9] && references.has(match[9])) {
        const button = make('button', match[9]);
        button.type = 'button';
        button.className = 'assistant-citation';
        button.setAttribute('aria-label', `查看证据 ${match[9]}`);
        button.addEventListener('click', () => {
          const details = references.get(match[9]);
          details.open = true;
          details.scrollIntoView({block: 'nearest', behavior: 'smooth'});
          details.querySelector('summary').focus();
        });
        parent.append(button);
      } else {
        parent.append(document.createTextNode(match[10] || match[0]));
      }
      offset = match.index + match[0].length;
    }
    parent.append(document.createTextNode(text.slice(offset)));
  }
  const item = line => /^(\s*)([-+*]|\d+[.)])\s+(.+)$/.exec(line);
  const fence = line => /^\s{0,3}(`{3,}|~{3,})(.*)$/.exec(line);
  const rule = line => /^\s{0,3}(?:\*\s*){3,}$|^\s{0,3}(?:-\s*){3,}$|^\s{0,3}(?:_\s*){3,}$/.test(line);
  function cells(line) {
    return line.trim().replace(/^\|/, '').replace(/\|$/, '').split(/(?<!\\)\|/).map(cell => cell.trim());
  }
  function tableStart(lines, i) {
    return i + 1 < lines.length && lines[i].includes('|') && cells(lines[i + 1]).every(cell => /^:?-{3,}:?$/.test(cell));
  }
  function blocks(parent, lines, references, depth = 0) {
    if (depth > 12) { parent.append(make('p', lines.join('\n'))); return; }
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      const opening = fence(line);
      if (opening) {
        const contents = [];
        const closing = new RegExp(`^\\s{0,3}${opening[1][0]}{${opening[1].length},}\\s*$`);
        i++;
        while (i < lines.length && !closing.test(lines[i])) contents.push(lines[i++]);
        if (i < lines.length) i++;
        const wrapper = make('div');
        wrapper.className = 'assistant-code-block';
        const language = opening[2].trim();
        if (language) wrapper.append(make('span', language.slice(0, 40)));
        const pre = make('pre');
        pre.append(make('code', contents.join('\n')));
        wrapper.append(pre); parent.append(wrapper); continue;
      }
      const heading = /^\s{0,3}(#{1,6})\s+(.+)$/.exec(line);
      if (heading) {
        const node = make(`h${Math.min(heading[1].length + 2, 6)}`);
        inline(node, heading[2].replace(/\s+#+\s*$/, ''), references);
        parent.append(node); i++; continue;
      }
      if (rule(line)) { parent.append(make('hr')); i++; continue; }
      if (tableStart(lines, i)) {
        const headers = cells(line);
        const wrapper = make('div'); wrapper.className = 'assistant-table-scroll';
        const table = make('table'); const head = make('thead'); const tr = make('tr');
        for (const text of headers) { const th = make('th'); inline(th, text, references); tr.append(th); }
        head.append(tr); table.append(head); i += 2;
        const body = make('tbody');
        while (i < lines.length && lines[i].trim() && lines[i].includes('|')) {
          const row = make('tr'); const values = cells(lines[i++]);
          for (let c = 0; c < headers.length; c++) {
            const td = make('td'); inline(td, values[c] || '', references); row.append(td);
          }
          body.append(row);
        }
        table.append(body); wrapper.append(table); parent.append(wrapper); continue;
      }
      if (/^\s{0,3}>/.test(line)) {
        const quote = [];
        while (i < lines.length && /^\s{0,3}>/.test(lines[i])) quote.push(lines[i++].replace(/^\s{0,3}> ?/, ''));
        const node = make('blockquote'); blocks(node, quote, references, depth + 1); parent.append(node); continue;
      }
      const first = item(line);
      if (first) {
        const indent = first[1].length;
        const ordered = /^\d/.test(first[2]);
        const list = make(ordered ? 'ol' : 'ul');
        if (ordered) list.start = Math.min(parseInt(first[2], 10), 100000);
        while (i < lines.length) {
          const current = item(lines[i]);
          if (!current || current[1].length !== indent || /^\d/.test(current[2]) !== ordered) break;
          const content = [current[3]];
          i++;
          while (i < lines.length) {
            if (!lines[i].trim()) {
              let next = i + 1;
              while (next < lines.length && !lines[next].trim()) next++;
              if (next >= lines.length || lines[next].search(/\S/) <= indent) break;
              content.push(''); i++; continue;
            }
            if (lines[i].search(/\S/) <= indent) break;
            const continuation = lines[i++];
            content.push(continuation.slice(Math.min(indent + 2, continuation.search(/\S/))));
          }
          const li = make('li'); blocks(li, content, references, depth + 1); list.append(li);
          let next = i;
          while (next < lines.length && !lines[next].trim()) next++;
          const following = item(lines[next] || '');
          if (following && following[1].length === indent && /^\d/.test(following[2]) === ordered) i = next;
          else break;
        }
        parent.append(list); continue;
      }
      const paragraph = [line]; i++;
      while (i < lines.length && lines[i].trim() && !fence(lines[i]) && !item(lines[i]) && !rule(lines[i]) &&
             !/^\s{0,3}(?:#{1,6}\s|>)/.test(lines[i]) && !tableStart(lines, i)) paragraph.push(lines[i++]);
      const node = make('p');
      paragraph.forEach((text, index) => {
        if (index) node.append(make('br'));
        inline(node, text, references);
      });
      parent.append(node);
    }
  }
  window.renderAssistantMarkdown = (text, references = new Map()) => {
    const root = make('div'); root.className = 'assistant-markdown';
    blocks(root, String(text).replace(/\r\n?/g, '\n').replace(/\t/g, '    ').split('\n'), references);
    return root;
  };
})();
