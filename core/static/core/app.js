(() => {
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
})();
