(() => {
  const savedTheme = localStorage.getItem('minivle-theme') || 'charcoal';
  document.documentElement.dataset.theme = savedTheme;
  document.querySelectorAll('.toast').forEach((toast) => {
    window.setTimeout(() => toast.classList.add('is-hidden'), 4500);
  });

  document.querySelectorAll('input[type="file"]').forEach((input) => {
    input.addEventListener('change', () => {
      const label = input.closest('p, label')?.querySelector('label') || input.previousElementSibling;
      if (label && input.files?.[0]) label.dataset.filename = input.files[0].name;
    });
  });

  const courseList = document.querySelector('#course-list');
  const viewButtons = document.querySelectorAll('[data-course-view]');
  const setCourseView = (view) => {
    if (!courseList) return;
    courseList.classList.toggle('is-list', view === 'list');
    viewButtons.forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.courseView === view)));
    localStorage.setItem('minivle-course-view', view);
  };
  if (courseList && viewButtons.length) {
    setCourseView(localStorage.getItem('minivle-course-view') || 'cards');
    viewButtons.forEach((button) => button.addEventListener('click', () => setCourseView(button.dataset.courseView)));
  }

  if (courseList) {
    const clicksKey = 'minivle-course-clicks';
    let clicks = {};
    try { clicks = JSON.parse(localStorage.getItem(clicksKey) || '{}'); } catch (_) { clicks = {}; }
    const cards = Array.from(courseList.querySelectorAll('.course-tile'));
    cards
      .map((card, index) => ({ card, index, score: clicks[card.href] || 0 }))
      .sort((a, b) => b.score - a.score || a.index - b.index)
      .forEach(({ card }) => courseList.append(card));
    cards.forEach((card) => card.addEventListener('click', () => {
      clicks[card.href] = (clicks[card.href] || 0) + 1;
      localStorage.setItem(clicksKey, JSON.stringify(clicks));
    }));
  }

  const rosterTable = document.querySelector('[data-roster-table]');
  const rosterSearch = document.querySelector('[data-roster-search]');
  const rosterCount = document.querySelector('[data-roster-count]');
  if (rosterTable) {
    const body = rosterTable.tBodies[0];
    const rows = () => Array.from(body.querySelectorAll('[data-roster-row]'));
    const updateCount = () => {
      if (!rosterCount) return;
      const visible = rows().filter((row) => !row.hidden).length;
      rosterCount.textContent = `${visible} ${visible === 1 ? 'person' : 'people'}`;
    };
    rosterSearch?.addEventListener('input', () => {
      const fragment = rosterSearch.value.trim().toLocaleLowerCase();
      rows().forEach((row) => { row.hidden = Boolean(fragment) && !row.dataset.name.toLocaleLowerCase().includes(fragment); });
      updateCount();
    });
    rosterTable.querySelectorAll('[data-roster-sort]').forEach((button) => {
      button.addEventListener('click', () => {
        const key = button.dataset.rosterSort;
        const direction = button.getAttribute('aria-sort') === 'ascending' ? -1 : 1;
        rows().sort((a, b) => direction * a.dataset[key].localeCompare(b.dataset[key], undefined, { sensitivity: 'base' })).forEach((row) => body.append(row));
        rosterTable.querySelectorAll('[data-roster-sort]').forEach((item) => item.setAttribute('aria-sort', item === button ? (direction === 1 ? 'ascending' : 'descending') : 'none'));
      });
    });
    updateCount();
  }
  const peopleTable = document.querySelector('[data-people-table]');
  if (peopleTable) {
    const body = peopleTable.tBodies[0];
    const rows = () => Array.from(body.querySelectorAll('[data-people-row]'));
    peopleTable.querySelector('[data-people-sort]').addEventListener('click', (buttonEvent) => {
      const button = buttonEvent.currentTarget;
      const direction = button.getAttribute('aria-sort') === 'ascending' ? -1 : 1;
      rows().sort((a, b) => direction * a.dataset.name.localeCompare(b.dataset.name, undefined, { sensitivity: 'base' })).forEach((row) => body.append(row));
      button.setAttribute('aria-sort', direction === 1 ? 'ascending' : 'descending');
    });
  }
  document.querySelectorAll('[data-theme-choice]').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.themeChoice === savedTheme));
    button.addEventListener('click', () => {
      const theme = button.dataset.themeChoice;
      localStorage.setItem('minivle-theme', theme);
      document.documentElement.dataset.theme = theme;
      document.querySelectorAll('[data-theme-choice]').forEach((item) => item.setAttribute('aria-pressed', String(item.dataset.themeChoice === theme)));
    });
  });
})();
