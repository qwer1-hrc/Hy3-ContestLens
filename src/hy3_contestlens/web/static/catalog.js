(() => {
  const cards = [...document.querySelectorAll('#problem-cards .card')];
  const fields = ['contest', 'year', 'group', 'title'].map(name => document.getElementById(`filter-${name}`));
  function filter() {
    const [contest, year, group, title] = fields.map(field => field.value.trim().toLowerCase());
    let count = 0;
    for (const card of cards) {
      const show = (!contest || card.dataset.contest.toLowerCase() === contest) &&
        (!year || card.dataset.year === year) && (!group || card.dataset.group === group) &&
        (!title || card.dataset.search.toLowerCase().includes(title));
      card.hidden = !show;
      if (show) count++;
    }
    document.getElementById('catalog-count').textContent = `显示 ${count} / ${cards.length} 题`;
  }
  fields.forEach(field => field.addEventListener('input', filter));
  filter();
})();
