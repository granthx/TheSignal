import { h, clear, safeUrl, timeAgo, fmtDate, store, reducedMotion, apiUrl } from './dom.js';
import { initSudoku } from './sudoku.js';
import { initSnake } from './snake.js';
import { initNewsletter } from './newsletter.js';

const $ = (sel, root = document) => root.querySelector(sel);
const prefs = store();
const state = { edition: null, filter: null, meta: null };

document.documentElement.classList.add(window.gsap ? 'has-gsap' : 'no-gsap');

async function api(path) {
  const url = apiUrl(path);
  const res = await fetch(url, { headers: { Accept: 'application/json' } });
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}

/* ------------------------------------------------------------------ small components */
function impactBars(n) {
  const level = Math.max(1, Math.min(5, Number(n) || 1));
  const wrap = h('span', { class: 'impact', role: 'img', 'aria-label': `Impact ${level} of 5` });
  for (let i = 1; i <= 5; i++) wrap.append(h('i', { class: i <= level ? 'on' : '' }));
  return wrap;
}

function metaLine(story, { withImpact = true } = {}) {
  const bits = [h('b', { text: story.source })];
  const ago = timeAgo(story.published);
  if (ago) bits.push(h('span', { text: ago }));
  if (story.also && story.also.length) {
    bits.push(h('span', { class: 'also', title: `Also covered by ${story.also.join(', ')}` }, `+${story.also.length} outlets`));
  }
  if (withImpact) bits.push(h('span', {}, h('span', { class: 'impact-label', text: 'IMPACT' }), impactBars(story.impact)));
  return h('div', { class: 'meta' }, bits);
}

function entityTags(story) {
  if (!story.entities || !story.entities.length) return null;
  return h('div', { class: 'ent' }, story.entities.map((e) => h('span', { text: e })));
}

function whyBubble(story) {
  if (!story.why) return null;
  return h('div', { class: 'bubble' }, h('span', { class: 'who', text: 'WHY IT MATTERS' }), story.why);
}

function storyPanel(story, { wide = false } = {}) {
  return h(
    'article',
    { class: `panel${wide ? ' wide' : ''}`, dataset: { id: story.id } },
    h('h3', {}, h('a', { href: safeUrl(story.url), target: '_blank', rel: 'noopener noreferrer', text: story.headline })),
    whyBubble(story),
    entityTags(story),
    metaLine(story),
  );
}

/* ------------------------------------------------------------------ filtering */
const matchesFilter = (story) => !state.filter || (story.entities || []).includes(state.filter);

function visibleSections(ed) {
  return ed.sections
    .map((sec) => ({ ...sec, stories: sec.stories.filter(matchesFilter) }))
    .filter((sec) => sec.stories.length);
}

/* ------------------------------------------------------------------ render */
function renderTicker(ed) {
  const box = $('#ticker');
  clear(box);
  const items = (ed.ticker && ed.ticker.length ? ed.ticker : []).filter(Boolean);
  if (!items.length) { box.hidden = true; return; }
  box.hidden = false;
  const make = (hidden) => items.map((t) => h('span', { class: 'ticker-item', 'aria-hidden': hidden ? 'true' : null, text: t }));
  box.append(h('div', { class: 'ticker-track' }, make(false), make(true)));
}

function renderMasthead(ed) {
  $('#mast-date').textContent = fmtDate(ed.date);
  const bits = [];
  if (ed.edition_no) bits.push(`EDITION #${ed.edition_no}`);
  if (ed.generated_at) bits.push(`UPDATED ${timeAgo(ed.generated_at).toUpperCase()}`);
  const stats = ed.stats || {};
  if (stats.published) bits.push(`${stats.published} STORIES`);
  if (stats.articles_seen) bits.push(`FROM ${stats.articles_seen} SCANNED`);
  $('#mast-edition').textContent = bits.join('  ·  ');
  document.title = `The Signal · ${fmtDate(ed.date)}`;
}

function applyFilter(name) {
  state.filter = state.filter === name ? null : name;
  const ed = state.edition;
  renderChips(ed);
  renderNav(ed);
  renderBody();
  observeNav();
}

function renderChips(ed) {
  const box = clear($('#chips'));
  const counts = ed.watchlist || {};
  const names = Object.keys(counts);
  $('#watch').hidden = !names.length;
  for (const name of names) {
    const n = counts[name];
    const btn = h(
      'button',
      {
        class: `chip${n ? '' : ' zero'}`, type: 'button', 'aria-pressed': state.filter === name ? 'true' : 'false',
        title: n ? `Show only ${name} stories` : `Nothing on ${name} today`,
        onclick: () => applyFilter(name),
      },
      name.toUpperCase(), h('span', { class: 'n', text: String(n) }),
    );
    box.append(btn);
  }
}

const leadCarousel = {
  stories: [],
  currentIndex: 0,
  timerId: null,
  paused: false,
};

function clearLeadTimer() {
  if (leadCarousel.timerId) {
    clearInterval(leadCarousel.timerId);
    leadCarousel.timerId = null;
  }
}

function getLeadStories(ed) {
  const all = [ed.lead, ...ed.sections.flatMap((s) => s.stories)].filter(Boolean);
  const byId = new Map(all.map((s) => [s.id, s]));
  const topIds = (ed.top_ids || []).map((id) => byId.get(id)).filter(Boolean);
  const candidates = [];
  if (ed.lead) candidates.push(ed.lead);
  for (const s of topIds) {
    if (!candidates.some((c) => c.id === s.id)) candidates.push(s);
  }
  for (const s of all) {
    if (!candidates.some((c) => c.id === s.id)) candidates.push(s);
    if (candidates.length >= 5) break;
  }
  return candidates.filter(matchesFilter).slice(0, 5);
}

function renderLead(ed) {
  clearLeadTimer();
  const wrap = clear($('#lead-wrap'));
  const stories = getLeadStories(ed);
  if (!stories.length) return;

  leadCarousel.stories = stories;
  leadCarousel.currentIndex = 0;
  leadCarousel.paused = false;

  const article = h('article', { class: 'lead' });
  const timerTrack = h('div', { class: 'lead-timer-track', title: 'New story every 4 seconds' });
  const timerBar = h('div', { class: 'lead-timer-bar running' });
  timerTrack.append(timerBar);

  const mainCol = h('div', { class: 'lead-main' });
  const sideCol = h('aside', { class: 'lead-side', 'aria-label': 'Story rating' });

  article.append(timerTrack, mainCol, sideCol);
  wrap.append(article);

  function syncBigFive(index) {
    const listItems = document.querySelectorAll('.top5 li');
    listItems.forEach((li, i) => {
      li.classList.toggle('active', i === index);
    });
  }

  function displayStory(index, animated = true) {
    if (!stories[index]) return;
    const story = stories[index];
    const level = Math.max(1, Math.min(5, Number(story.impact) || 1));

    if (animated) {
      article.classList.add('is-transitioning');
      setTimeout(() => article.classList.remove('is-transitioning'), 220);
    }

    article.dataset.id = story.id;

    // Reset 4-second timer bar animation
    timerBar.classList.remove('running');
    void timerBar.offsetWidth; // trigger reflow
    if (!leadCarousel.paused) {
      timerBar.classList.add('running');
    }

    clear(mainCol);
    const badgeRow = h('div', { class: 'lead-badge-row' },
      h('div', { class: 'lead-badge-group' },
        h('span', { class: 'sfx', text: index === 0 ? 'TOP STORY!' : `HOT STORY #${index + 1}` }),
      ),
      h('div', { class: 'lead-controls' },
        h('span', { class: 'lead-counter mono', text: `${index + 1} / ${stories.length}` }),
        h('button', {
          class: 'lead-nav-btn prev',
          type: 'button',
          'aria-label': 'Previous story',
          onclick: (e) => { e.preventDefault(); prevStory(); }
        }, '‹'),
        h('button', {
          class: 'lead-nav-btn next',
          type: 'button',
          'aria-label': 'Next story',
          onclick: (e) => { e.preventDefault(); nextStory(); }
        }, '›'),
      ),
    );

    const elements = [
      badgeRow,
      h('h2', {}, h('a', { href: safeUrl(story.url), target: '_blank', rel: 'noopener noreferrer', text: story.headline })),
      whyBubble(story),
      entityTags(story),
      metaLine(story, { withImpact: false }),
    ].filter(Boolean);
    mainCol.append(...elements);

    clear(sideCol);
    sideCol.append(
      h('div', { class: 'big-impact', 'aria-hidden': 'true' }, String(level), h('small', { text: '/5' })),
      h('div', { class: 'mono small' }, 'IMPACT RATING'),
      impactBars(level),
      h('div', { class: 'mono small', text: story.beat && ed.beat_titles ? (ed.beat_titles[story.beat] || '').toUpperCase() : '' }),
    );

    syncBigFive(index);
  }

  function nextStory() {
    leadCarousel.currentIndex = (leadCarousel.currentIndex + 1) % stories.length;
    displayStory(leadCarousel.currentIndex, true);
  }

  function prevStory() {
    leadCarousel.currentIndex = (leadCarousel.currentIndex - 1 + stories.length) % stories.length;
    displayStory(leadCarousel.currentIndex, true);
  }

  // Hover pauses 4-second rotation
  article.addEventListener('mouseenter', () => {
    leadCarousel.paused = true;
    timerBar.style.animationPlayState = 'paused';
  });
  article.addEventListener('mouseleave', () => {
    leadCarousel.paused = false;
    timerBar.style.animationPlayState = 'running';
  });

  // 4-second rotation cycle
  if (stories.length > 1) {
    leadCarousel.timerId = setInterval(() => {
      if (!leadCarousel.paused) {
        nextStory();
      }
    }, 4000);
  }

  window.__selectLeadStory = (idx) => {
    leadCarousel.currentIndex = idx;
    displayStory(idx, true);
  };

  displayStory(0, false);
}

function renderTop(ed) {
  const wrap = clear($('#top-wrap'));
  if (state.filter) return; // the rail is a "whole paper" view
  const all = [ed.lead, ...ed.sections.flatMap((s) => s.stories)].filter(Boolean);
  const byId = new Map(all.map((s) => [s.id, s]));
  const top = (ed.top_ids || []).map((id) => byId.get(id)).filter(Boolean).slice(0, 5);
  if (top.length < 3) return;

  const ol = h('ol', {});
  top.forEach((s, i) => {
    const li = h('li', {
      class: i === (leadCarousel.currentIndex || 0) ? 'active' : '',
      dataset: { index: String(i) },
      onclick: () => {
        if (window.__selectLeadStory) {
          window.__selectLeadStory(i);
        }
      }
    },
      h('span', { class: 'rank', 'aria-hidden': 'true', text: String(i + 1) }),
      h('a', {
        href: safeUrl(s.url),
        target: '_blank',
        rel: 'noopener noreferrer',
        text: s.headline,
        onclick: (e) => {
          if (window.__selectLeadStory) window.__selectLeadStory(i);
        }
      }),
      h('span', { class: 'src', text: s.source }),
    );
    ol.append(li);
  });

  wrap.append(
    h('section', { class: 'top5', 'aria-label': 'Top five by impact' },
      h('div', { class: 'top5-head' },
        h('h2', { text: 'THE BIG FIVE' }),
        h('span', { class: 'mono top5-hint', text: 'CLICK ANY STORY TO SPOTLIGHT' }),
      ),
      ol,
    ),
  );
}

/** Column spans on a 6-column grid: one big panel first, then rows of three, last row stretched to fill. */
function panelSpans(n) {
  if (n <= 1) return [6];
  const spans = [4, 2];
  let rest = n - 2;
  while (rest >= 3) { spans.push(2, 2, 2); rest -= 3; }
  if (rest === 2) spans.push(3, 3);
  else if (rest === 1) spans.push(6);
  return spans;
}

function renderChapters(ed) {
  const wrap = clear($('#chapters'));
  const sections = visibleSections(ed);
  sections.forEach((sec, idx) => {
    const grid = h('div', { class: 'grid' });
    const spans = panelSpans(sec.stories.length);
    sec.stories.forEach((story, i) => {
      const panel = storyPanel(story, { wide: spans[i] >= 4 });
      panel.classList.add(`s${spans[i]}`);
      grid.append(panel);
    });
    wrap.append(
      h('section', { class: 'chapter', id: `ch-${sec.id}`, 'aria-labelledby': `h-${sec.id}` },
        h('div', { class: 'section-head' },
          h('span', { class: 'chapter-no mono', text: `CH.${String(idx + 1).padStart(2, '0')}` }),
          h('h2', { id: `h-${sec.id}`, text: sec.title.toUpperCase() }),
          h('p', { class: 'kicker', text: sec.kicker }),
        ),
        grid,
      ),
    );
  });

  const empty = $('#empty');
  const nothing = !sections.length && !(ed.lead && matchesFilter(ed.lead));
  empty.hidden = !nothing;
  if (nothing) {
    clear(empty).append(
      h('b', { text: state.filter ? `NOTHING ON ${state.filter.toUpperCase()} TODAY` : 'A QUIET DAY' }),
      h('span', { text: state.filter ? 'Clear the filter to see the rest of the paper.' : 'Nothing cleared the relevance bar. That is the point of a no-nonsense paper.' }),
    );
  }
}

function renderReads(ed) {
  const wrap = clear($('#reads-wrap'));
  const reads = ed.reads || [];
  wrap.hidden = !reads.length || !!state.filter;
  if (wrap.hidden) return;
  wrap.append(
    h('div', { class: 'reads-inner' },
      h('div', { class: 'section-head' },
        h('span', { class: 'chapter-no mono', text: 'BRAIN FUEL' }),
        h('h2', { id: 'reads-title', text: 'SMART READS' }),
        h('p', { class: 'kicker', text: 'Not news. Ideas that rewire how you think about minds, machines and the future.' }),
      ),
      h('div', { class: 'reads-grid' }, reads.map((r, i) =>
        h('article', { class: 'read' },
          h('span', { class: 'num', 'aria-hidden': 'true', text: String(i + 1) }),
          h('h3', {}, h('a', { href: safeUrl(r.url), target: '_blank', rel: 'noopener noreferrer', text: r.title })),
          h('p', { text: r.hook }),
          h('div', { class: 'meta' }, h('b', { text: r.source }), r.read_minutes ? h('span', { text: `${r.read_minutes} min read` }) : null),
          h('a', { class: 'go', href: safeUrl(r.url), target: '_blank', rel: 'noopener noreferrer', 'aria-label': `Read ${r.title}` }, 'READ →'),
        ))),
    ),
  );
}

function renderNav(ed) {
  const nav = clear($('#nav'));
  const links = [];
  if ((ed.lead && matchesFilter(ed.lead)) || !state.filter) {
    links.push(['#front-page', 'FRONT PAGE']);
  }
  const visible = visibleSections(ed);
  for (const sec of visible) {
    links.push([`#ch-${sec.id}`, sec.title.toUpperCase()]);
  }
  if ((ed.reads || []).length && !state.filter) {
    links.push(['#reads-wrap', 'BRAIN FUEL']);
  }
  links.push(['#play', 'PLAY']);
  for (const [href, label] of links) nav.append(h('a', { href, text: label }));
  nav.append(h('a', { class: 'cta', href: '#subscribe', text: 'GET IT DAILY' }));
}

function renderFooter(ed) {
  const s = ed.stats || {};
  const parts = [];
  if (ed.mode === 'demo') parts.push('Sample content, no live edition built yet.');
  else {
    parts.push(`Compiled from ${s.sources_ok ?? 0} sources`);
    if (s.articles_seen) parts.push(`${s.articles_seen} articles scanned`);
    if (s.unique_stories) parts.push(`${s.unique_stories} unique stories`);
    if (s.cleared_the_bar) parts.push(`${s.cleared_the_bar} cleared the relevance bar`);
    parts.push(s.llm ? 'AI editor on' : 'extractive summaries (no AI editor key set)');
  }
  $('#foot-stats').textContent = parts.join(' · ');
  const failed = s.failed_sources || [];
  $('#foot-failed').hidden = !failed.length;
  const list = clear($('#foot-failed-list'));
  failed.forEach((f) => list.append(h('li', { text: `${f.name}: ${f.error}` })));
}

/* ------------------------------------------------------------------ motion */
function animateBody() {
  if (!window.gsap || reducedMotion()) return;
  const { gsap, ScrollTrigger } = window;
  if (ScrollTrigger) { gsap.registerPlugin(ScrollTrigger); ScrollTrigger.getAll().forEach((t) => t.kill()); }

  const sfx = $('.sfx');
  if (sfx) gsap.from(sfx, { scale: 3, rotate: -14, autoAlpha: 0, duration: 0.5, ease: 'back.out(2)', delay: 0.15 });
  const lead = $('.lead');
  if (lead) gsap.from(lead, { y: 24, autoAlpha: 0, duration: 0.5, ease: 'power3.out' });

  const targets = gsap.utils.toArray('.panel, .top5, .read');
  if (!targets.length) return;
  const reveal = (batch) => gsap.fromTo(
    batch,
    { y: 30, scale: 1.04, rotate: () => gsap.utils.random(-1.2, 1.2), autoAlpha: 0 },
    { y: 0, scale: 1, rotate: 0, autoAlpha: 1, duration: 0.5, stagger: 0.07, ease: 'back.out(1.6)', overwrite: true },
  );
  if (ScrollTrigger) {
    gsap.set(targets, { autoAlpha: 0 });
    ScrollTrigger.batch(targets, { start: 'top 94%', once: true, onEnter: reveal });
  } else {
    reveal(targets);
  }
}

function splitLogo() {
  const logo = $('#logo');
  if (!logo || logo.dataset.split) return;
  logo.dataset.split = '1';
  const text = logo.textContent;
  clear(logo);
  [...text].forEach((c) => logo.append(h('span', { class: 'ch', 'aria-hidden': 'true', text: c === ' ' ? ' ' : c })));
  if (window.gsap && !reducedMotion()) {
    window.gsap.from('#logo .ch', { y: -60, rotate: () => window.gsap.utils.random(-25, 25), autoAlpha: 0, duration: 0.55, stagger: 0.045, ease: 'back.out(2.2)' });
  }
}

let navScrollHandler = null;
function observeNav() {
  if (navScrollHandler) {
    window.removeEventListener('scroll', navScrollHandler);
    navScrollHandler = null;
  }
  const nav = $('#nav');
  if (!nav) return;

  const links = [...nav.querySelectorAll('a[href^="#"]')];
  let isClickScrolling = false;
  let clickTimeout = null;

  const getNavHeight = () => nav.getBoundingClientRect().height || 50;

  const updateActiveNav = () => {
    if (isClickScrolling) return;

    const scrollY = window.scrollY || window.pageYOffset || 0;
    const navHeight = getNavHeight();

    // 1. If at or near the very top of the page, FRONT PAGE is always active
    if (scrollY <= 140) {
      links.forEach((a) => {
        a.classList.toggle('on', a.getAttribute('href') === '#front-page');
      });
      return;
    }

    // 2. If scrolled to the bottom of the page, activate the last visible target
    const scrollBottom = window.innerHeight + scrollY;
    const totalHeight = document.documentElement.scrollHeight;
    if (scrollBottom >= totalHeight - 60) {
      for (let i = links.length - 1; i >= 0; i--) {
        const id = links[i].getAttribute('href').slice(1);
        const target = document.getElementById(id);
        if (target && target.isConnected && !target.hidden && target.offsetHeight > 0) {
          links.forEach((a) => a.classList.toggle('on', a === links[i]));
          return;
        }
      }
    }

    // 3. Middle of page: check which visible section intersects the focal viewport line
    const focalPoint = navHeight + 70;
    let activeLink = null;

    for (const a of links) {
      const id = a.getAttribute('href').slice(1);
      const target = document.getElementById(id);
      if (!target || !target.isConnected || target.hidden || target.offsetHeight === 0) {
        continue;
      }
      const rect = target.getBoundingClientRect();
      if (rect.top <= focalPoint && rect.bottom > focalPoint) {
        activeLink = a;
        break;
      }
    }

    // Fallback: closest section above focalPoint
    if (!activeLink) {
      let closestDistance = -Infinity;
      for (const a of links) {
        const id = a.getAttribute('href').slice(1);
        const target = document.getElementById(id);
        if (!target || !target.isConnected || target.hidden || target.offsetHeight === 0) continue;
        const rect = target.getBoundingClientRect();
        if (rect.top <= focalPoint && rect.top > closestDistance) {
          closestDistance = rect.top;
          activeLink = a;
        }
      }
    }

    if (!activeLink && links.length) {
      activeLink = links[0];
    }

    links.forEach((a) => {
      a.classList.toggle('on', a === activeLink);
    });
  };

  links.forEach((a) => {
    a.addEventListener('click', (e) => {
      const href = a.getAttribute('href');
      const id = href.slice(1);
      const target = document.getElementById(id);
      if (target) {
        e.preventDefault();
        isClickScrolling = true;
        if (clickTimeout) clearTimeout(clickTimeout);

        links.forEach((other) => other.classList.toggle('on', other === a));

        const navHeight = getNavHeight();
        let targetY = 0;
        if (id !== 'front-page') {
          const rect = target.getBoundingClientRect();
          targetY = Math.max(0, rect.top + (window.scrollY || window.pageYOffset || 0) - navHeight - 12);
        }

        window.scrollTo({
          top: targetY,
          behavior: 'smooth',
        });

        clickTimeout = setTimeout(() => {
          isClickScrolling = false;
          updateActiveNav();
        }, 550);

        history.replaceState(null, '', `#${id}`);
      }
    });
  });

  let ticking = false;
  navScrollHandler = () => {
    if (!ticking) {
      requestAnimationFrame(() => {
        updateActiveNav();
        ticking = false;
      });
      ticking = true;
    }
  };

  window.addEventListener('scroll', navScrollHandler, { passive: true });
  updateActiveNav();
}

function trackProgress() {
  const bar = $('#progress');
  const update = () => {
    const max = document.documentElement.scrollHeight - innerHeight;
    bar.style.transform = `scaleX(${max > 0 ? Math.min(1, scrollY / max) : 0})`;
  };
  addEventListener('scroll', update, { passive: true });
  addEventListener('resize', update);
  update();
}

/* ------------------------------------------------------------------ orchestration */
function renderBody() {
  const ed = state.edition;
  renderLead(ed);
  renderTop(ed);
  renderChapters(ed);
  renderReads(ed);
  animateBody();
}

function renderEdition(ed) {
  state.edition = ed;
  state.filter = null;
  renderTicker(ed);
  renderMasthead(ed);
  renderChips(ed);
  renderNav(ed);
  renderBody();
  observeNav();
  renderFooter(ed);
}

async function loadEdition(date) {
  try {
    const data = await api(date ? `/api/edition?date=${encodeURIComponent(date)}` : '/api/edition');
    if (!data) throw new Error('Empty response from edition API.');
    renderEdition(data);
  } catch (err) {
    const empty = $('#empty');
    empty.hidden = false;
    clear(empty).append(h('b', { text: 'COULDN\'T LOAD THE PAPER' }), h('span', { text: err.message }));
  }
}

async function loadArchive(currentDate) {
  const select = clear($('#archive'));
  try {
    const { editions } = await api('/api/editions');
    if (!editions.length) { $('.archive').hidden = true; return; }
    for (const e of editions) {
      select.append(h('option', { value: e.date, selected: e.date === currentDate, text: `${fmtDate(e.date)}  ·  #${e.edition_no}` }));
    }
    select.onchange = () => loadEdition(select.value);
  } catch { $('.archive').hidden = true; }
}

function initTheme() {
  const btn = $('#theme');
  const apply = (theme) => {
    document.documentElement.dataset.theme = theme;
    btn.setAttribute('aria-pressed', theme === 'ink' ? 'true' : 'false');
    btn.textContent = theme === 'ink' ? '◑ PAPER' : '◐ INK';
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'ink' ? '#0f0f12' : '#f6f3ea');
  };
  const saved = prefs.get('signal.theme');
  apply(saved || (matchMedia('(prefers-color-scheme: dark)').matches ? 'ink' : 'paper'));
  btn.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'ink' ? 'paper' : 'ink';
    apply(next);
    prefs.set('signal.theme', next);
  });
}

function initUpdateBtn() {
  const btn = $('#btn-update');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const originalText = btn.textContent;
    btn.classList.add('is-updating');
    btn.textContent = '⚡ REFRESHING...';
    try {
      const res = await fetch(apiUrl('/api/refresh'), {
        method: 'POST',
        headers: { Accept: 'application/json' },
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        throw new Error((data && data.error) || `Update failed (${res.status})`);
      }
      btn.textContent = '✓ UPDATED!';
      renderEdition(data);
      loadArchive(data.date);
      setTimeout(() => {
        btn.classList.remove('is-updating');
        btn.textContent = originalText;
      }, 2500);
    } catch (err) {
      alert(err.message);
      btn.classList.remove('is-updating');
      btn.textContent = originalText;
    }
  });
}

function initGameTabs(snakeInstance) {
  const tabSudoku = $('#btn-tab-sudoku');
  const tabSnake = $('#btn-tab-snake');
  const sudokuPane = $('#sudoku');
  const snakePane = $('#snake-game');

  if (!tabSudoku || !tabSnake || !sudokuPane || !snakePane) return;

  tabSudoku.addEventListener('click', () => {
    tabSudoku.classList.add('active');
    tabSudoku.setAttribute('aria-selected', 'true');
    tabSnake.classList.remove('active');
    tabSnake.setAttribute('aria-selected', 'false');

    sudokuPane.hidden = false;
    snakePane.hidden = true;
    if (snakeInstance && snakeInstance.pause) snakeInstance.pause();
  });

  tabSnake.addEventListener('click', () => {
    tabSnake.classList.add('active');
    tabSnake.setAttribute('aria-selected', 'true');
    tabSudoku.classList.remove('active');
    tabSudoku.setAttribute('aria-selected', 'false');

    snakePane.hidden = false;
    sudokuPane.hidden = true;
  });
}

async function main() {
  initTheme();
  initUpdateBtn();
  splitLogo();
  trackProgress();

  try { state.meta = await api('/api/meta'); } catch { state.meta = null; }
  const confetti = (opts) => { if (window.confetti && !reducedMotion()) window.confetti(opts); };
  initSudoku($('#sudoku'), { confetti });
  const snakeInstance = initSnake($('#snake-game'), { confetti });
  initGameTabs(snakeInstance);
  initNewsletter({ meta: state.meta });

  const requested = new URLSearchParams(location.search).get('date');
  await loadEdition(requested);
  loadArchive(state.edition?.date);

  if (location.hash && location.hash.length > 1) {
    const id = location.hash.slice(1);
    const scrollTarget = () => {
      const el = document.getElementById(id);
      if (el) {
        const nav = $('#nav');
        const navH = nav ? nav.getBoundingClientRect().height : 50;
        const rect = el.getBoundingClientRect();
        const topY = Math.max(0, rect.top + (window.scrollY || window.pageYOffset || 0) - navH - 14);
        window.scrollTo({ top: topY, behavior: 'instant' });
      }
    };
    scrollTarget();
    setTimeout(scrollTarget, 120);
    setTimeout(scrollTarget, 350);
  }
}

main();
