// Global State for Static Mode
let GLOBAL_DB = null;
let IS_STATIC = false;
let DATA_CACHE = null;
let CACHE_TIME = 0;
const CACHE_TTL = 30000; // 30 seconds

function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// Decode a hash-route segment; falls back to the raw value on malformed input
function safeDecode(segment) {
  try { return decodeURIComponent(segment); } catch (e) { return segment; }
}

// Shared fallback UI when vis.js fails to load from CDN
function visFallbackHtml() {
  return '<div style="padding:2rem;text-align:center;color:var(--text-secondary);"><p>⚠️ 图谱库加载失败，请检查网络连接后刷新页面</p><button class="primary" onclick="location.reload()">🔄 刷新</button></div>';
}

function shuffle(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function showToast(msg) {
  let stack = document.getElementById('toast-stack');
  if (!stack) {
    stack = document.createElement('div');
    stack.id = 'toast-stack';
    stack.className = 'toast-stack';
    document.body.appendChild(stack);
  }
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  stack.appendChild(t);
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 400); }, 3400);
}

// ===== Achievement system (localStorage) =====
const AchievementManager = {
  KEY: 'kg_achievements_v1',
  defs: [
    { id: 'first_visit', icon: '🚪', name: '初来乍到', desc: '首次踏进知识博物馆' },
    { id: 'scholar_10', icon: '🔍', name: '博物馆常客', desc: '浏览 10 位学者的主页', goal: 10, progress: s => s.visitedScholars.length },
    { id: 'scholar_50', icon: '🏛️', name: '半个图书馆', desc: '浏览 50 位学者的主页', goal: 50, progress: s => s.visitedScholars.length },
    { id: 'school_complete', icon: '🎓', name: '学派收藏家', desc: '完整浏览一个学派（≥3人）的全部学者' },
    { id: 'quiz_7', icon: '🥉', name: '竞答能手', desc: '知识竞答单轮答对 7 题' },
    { id: 'quiz_10', icon: '🏆', name: '竞答状元', desc: '知识竞答单轮全部答对' },
    { id: 'path_master', icon: '🧭', name: '连接大师', desc: '在连接游戏中准确猜中路径 3 次', goal: 3, progress: s => s.pathWins },
    { id: 'tour_done', icon: '🎫', name: '导览学员', desc: '完整走完一条主题导览' },
    { id: 'dark_mode', icon: '🌙', name: '夜猫子', desc: '使用暗色主题浏览博物馆' },
    { id: 'searcher', icon: '🔎', name: '检索员', desc: '使用一次全局搜索' }
  ],
  state: null,
  load() {
    try { this.state = JSON.parse(localStorage.getItem(this.KEY)); } catch (e) { this.state = null; }
    if (!this.state || typeof this.state !== 'object') {
      this.state = { unlocked: {}, visitedScholars: [], pathWins: 0, quizBest: 0, toursDone: [] };
    }
  },
  save() { try { localStorage.setItem(this.KEY, JSON.stringify(this.state)); } catch (e) {} },
  unlock(id) {
    if (this.state.unlocked[id]) return;
    this.state.unlocked[id] = Date.now();
    this.save();
    const d = this.defs.find(x => x.id === id);
    if (d) showToast(`🏆 解锁成就「${d.name}」`);
  },
  checkProgress() {
    for (const d of this.defs) {
      if (this.state.unlocked[d.id] || !d.progress) continue;
      if (d.progress(this.state) >= d.goal) this.unlock(d.id);
    }
  },
  visitScholar(id, data) {
    this.unlock('first_visit');
    if (!this.state.visitedScholars.includes(id)) {
      this.state.visitedScholars.push(id);
      this.save();
    }
    this.checkProgress();
    if (data && data.scholars) {
      const me = data.scholars.find(s => s.scholar_id === id);
      if (me && me.school_id) {
        const peers = data.scholars.filter(s => s.school_id === me.school_id);
        if (peers.length >= 3 && peers.every(p => this.state.visitedScholars.includes(p.scholar_id))) {
          this.unlock('school_complete');
        }
      }
    }
  },
  quizScore(n) {
    if (n > this.state.quizBest) { this.state.quizBest = n; this.save(); }
    if (n >= 7) this.unlock('quiz_7');
    if (n >= 10) this.unlock('quiz_10');
  },
  pathWin() { this.state.pathWins++; this.save(); this.checkProgress(); },
  tourCompleted(id) {
    if (!this.state.toursDone.includes(id)) this.state.toursDone.push(id);
    this.save();
    this.unlock('tour_done');
  },
  darkMode() { this.unlock('dark_mode'); },
  searched() { this.unlock('searcher'); }
};

// ===== Focus mode for vis.js networks: click to dim non-neighbors =====
function makeFocusMode(network, opts) {
  const options = opts || {};
  const container = network.body.container;
  let focusBar = container.querySelector('.focus-bar');
  if (!focusBar) {
    focusBar = document.createElement('div');
    focusBar.className = 'focus-bar';
    container.appendChild(focusBar);
  }
  const nodeState = new Map();
  const edgeState = new Map();

  function clearFocus() {
    nodeState.forEach((s, id) => network.body.data.nodes.update({ id, color: s.color, font: s.font }));
    nodeState.clear();
    edgeState.forEach((s, id) => network.body.data.edges.update({ id, color: s.color }));
    edgeState.clear();
    focusBar.style.display = 'none';
  }

  network.on('click', (params) => {
    if (!params.nodes.length) { clearFocus(); return; }
    const id = params.nodes[0];
    clearFocus();
    const nodes = network.body.data.nodes.get();
    const edges = network.body.data.edges.get();
    const keep = new Set([id]);
    edges.forEach((e) => {
      if (e.from === id) keep.add(e.to);
      if (e.to === id) keep.add(e.from);
    });
    nodes.forEach((n) => {
      nodeState.set(n.id, { color: n.color, font: n.font });
      if (keep.has(n.id)) return;
      network.body.data.nodes.update({
        id: n.id,
        color: { background: '#b9c3cc', border: '#9aa7b2', highlight: { background: '#b9c3cc', border: '#9aa7b2' } },
        font: { color: '#a8b0b8' }
      });
    });
    edges.forEach((e) => {
      edgeState.set(e.id, { color: e.color });
      const connected = e.from === id || e.to === id;
      network.body.data.edges.update({
        id: e.id,
        color: connected ? { color: '#e74c3c', highlight: '#e74c3c' } : { color: 'rgba(150,160,170,0.25)' }
      });
    });
    const n = nodes.find(x => x.id === id);
    const label = options.label ? options.label(n) : (n && (n.label || n.id)) || id;
    focusBar.style.display = 'flex';
    focusBar.innerHTML = `<span>🎯 聚焦：${escapeHtml(label)}</span>` +
      (options.navigate ? `<button class="focus-go" data-id="${escapeHtml(id)}">查看详情 →</button>` : '') +
      `<button class="focus-close" title="取消聚焦">✕</button>`;
    const go = focusBar.querySelector('.focus-go');
    if (go) go.addEventListener('click', (ev) => {
      const goId = ev.currentTarget.getAttribute('data-id');
      if (goId && options.navigate) options.navigate(goId);
    });
    focusBar.querySelector('.focus-close').addEventListener('click', clearFocus);
  });

  network.on('doubleClick', (params) => {
    if (params.nodes.length && options.navigate) options.navigate(params.nodes[0]);
  });
}

function getGraphFontColor() {
  return document.documentElement.getAttribute('data-theme') === 'dark' ? '#eaeaea' : '#333';
}

// Theme Management
const ThemeManager = {
  init() {
    const saved = localStorage.getItem('theme');
    if (saved) {
      document.documentElement.setAttribute('data-theme', saved);
      this.updateIcon(saved);
    } else if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
      document.documentElement.setAttribute('data-theme', 'dark');
      this.updateIcon('dark');
    }
  },
  toggle() {
    const current = document.documentElement.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
    this.updateIcon(next);
  },
  updateIcon(theme) {
    const icon = document.getElementById('themeIcon');
    if (icon) icon.textContent = theme === 'dark' ? '☀️' : '🌙';
  }
};

const API = {
  init: async () => {
    ThemeManager.init();

    try {
      const res = await fetch('/api/status');
      if (res.ok) {
        IS_STATIC = false;
        document.getElementById('statusPill').innerText = 'Online';
        document.getElementById('statusPill').classList.add('ok');
      } else {
        throw new Error('Not dynamic');
      }
    } catch (e) {
      IS_STATIC = true;
      document.getElementById('statusPill').innerText = 'Museum Mode';
      document.getElementById('statusPill').style.background = 'var(--accent-light)';
      document.getElementById('statusPill').style.color = 'var(--accent-color)';

      const dbRes = await fetch('/api/db.json');
      if (dbRes.ok) {
        GLOBAL_DB = await dbRes.json();
      } else {
        console.error('Failed to load static database');
      }
    }
  },

  browse: async () => {
    // Use cache if available and fresh
    if (DATA_CACHE && Date.now() - CACHE_TIME < CACHE_TTL) {
      return DATA_CACHE;
    }

    let data;
    if (IS_STATIC) {
      if (!GLOBAL_DB) {
        throw new Error('静态数据库 (api/db.json) 加载失败，请刷新页面重试');
      }
      data = {
        scholars: GLOBAL_DB.scholars || [],
        propositions: GLOBAL_DB.propositions || [],
        passages: GLOBAL_DB.passages || [],
        concepts: GLOBAL_DB.concepts || [],
        books: GLOBAL_DB.books || [],
        relations: GLOBAL_DB.relations || [],
        influences: GLOBAL_DB.influences || [],
        quotes: GLOBAL_DB.quotes || []
      };
    } else {
      const res = await fetch('/api/browse');
      if (!res.ok) throw new Error(`服务器返回 ${res.status}，请稍后重试`);
      data = await res.json();
      data.quotes = data.quotes || [];
    }

    // Update cache
    DATA_CACHE = data;
    CACHE_TIME = Date.now();
    return data;
  },

  list_csv: async () => {
    if (IS_STATIC) return { files: [] };
    const res = await fetch('/api/list');
    return res.json();
  },

  validate: async () => {
    if (IS_STATIC) {
      return {
        summary: { errors: 0, warnings: 0 },
        errors: [],
        static_msg: "Static Mode: Data is pre-validated."
      };
    }
    const res = await fetch('/api/validate', { method: 'POST' });
    let data = null;
    try { data = await res.json(); } catch (_) {}
    if (!res.ok && res.status !== 400) {
      throw new Error((data && data.error) || `校验请求失败 (${res.status})`);
    }
    if (!data) throw new Error('校验响应格式错误');
    return data;
  },

  get_csv: async (name) => {
    const res = await fetch(`/api/csv?name=${encodeURIComponent(name)}`);
    if (!res.ok && res.status !== 400 && res.status !== 404) {
      throw new Error(`服务器返回 ${res.status}`);
    }
    return res.json();
  },

  save_csv: async (name, content) => {
    try {
      const res = await fetch('/api/csv', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({name, content})
      });
      DATA_CACHE = null; // Invalidate cache
      return await res.json();
    } catch (e) {
      DATA_CACHE = null;
      throw e;
    }
  },

  search: async (query) => {
    const data = await API.browse();
    const q = query.toLowerCase();
    const results = {
      scholars: (data.scholars || []).filter(s =>
        (s.name_zh || '').toLowerCase().includes(q) ||
        (s.name_en && s.name_en.toLowerCase().includes(q)) ||
        (s.description_zh && s.description_zh.toLowerCase().includes(q))
      ),
      books: (data.books || []).filter(b =>
        (b.title_zh || '').toLowerCase().includes(q) ||
        (b.title_en && b.title_en.toLowerCase().includes(q)) ||
        (b.description_zh && b.description_zh.toLowerCase().includes(q))
      ),
      propositions: (data.propositions || []).filter(p =>
        (p.proposition_text_zh || '').toLowerCase().includes(q)
      )
    };
    return results;
  }
};

const VIEWS = {
  dashboard: async () => {
    const data = await API.browse();
    const influenceCount = data.influences ? data.influences.length : 0;

    return `
      <h2><span class="icon">📊</span> 新闻传播学知识博物馆</h2>

      <div class="stats-grid">
        <div class="stat-card" onclick="window.location.hash='scholars_list'">
          <div class="stat-icon">👥</div>
          <div class="stat-value">${data.scholars.length}</div>
          <div class="stat-label">位学者</div>
        </div>
        <div class="stat-card" onclick="window.location.hash='timeline'">
          <div class="stat-icon">📜</div>
          <div class="stat-value">${data.propositions.length}</div>
          <div class="stat-label">个历史节点</div>
        </div>
        <div class="stat-card" onclick="window.location.hash='influence'">
          <div class="stat-icon">🔗</div>
          <div class="stat-value">${influenceCount}</div>
          <div class="stat-label">条影响关系</div>
        </div>
        <div class="stat-card" onclick="window.location.hash='browse'">
          <div class="stat-icon">📖</div>
          <div class="stat-value">${data.passages.length}</div>
          <div class="stat-label">条核心证据</div>
        </div>
      </div>

      <div class="card-grid">
        <div class="card featured clickable" onclick="window.location.hash='influence'">
          <h3>🔗 学术影响网络</h3>
          <p>探索 ${influenceCount} 条学术传承与影响关系，追溯思想脉络的演进。</p>
        </div>
        <div class="card clickable" onclick="window.location.hash='timeline'">
          <h3>⏳ 历史时间轴</h3>
          <p>从 1644 年弥尔顿《论出版自由》到当代传播理论，纵览新闻传播史演变。</p>
        </div>
        <div class="card clickable" onclick="window.location.hash='graph'">
          <h3>🕸️ 知识图谱</h3>
          <p>可视化学者、理论与学派之间的复杂关联网络。</p>
        </div>
      </div>

      <div style="margin-top: 2rem; padding: 1.5rem; background: var(--card-bg); border-radius: 12px; border: 1px solid var(--border-color);">
        <h3 style="margin-top:0;">快速入门</h3>
        <p style="color: var(--text-secondary); line-height: 1.8;">
          👉 <b>学者名录</b>：浏览 ${data.scholars.length} 位中西新闻传播思想家<br>
          👉 <b>影响网络</b>：查看学术传承关系的可视化图谱<br>
          👉 <b>经典著作</b>：探索改变历史的 ${(data.books || []).length} 部学术名著<br>
          👉 <b>证据库</b>：每一条断言均有公开文献支撑
        </p>
      </div>

      <div style="margin-top: 2rem; padding: 1.5rem; background: var(--card-bg); border-radius: 12px; border: 1px solid var(--border-color);">
        <h3 style="margin-top:0;">📦 数据导出</h3>
        <p style="color: var(--text-secondary); margin-bottom: 1rem;">导出知识图谱数据以供其他系统使用</p>
        <div style="display: flex; gap: 1rem; flex-wrap: wrap;">
          <button class="primary" onclick="exportJSON()">📄 导出 JSON</button>
          ${!IS_STATIC ? '<button class="primary" onclick="exportRDF()">🔗 导出 RDF/Turtle</button>' : ''}
        </div>
      </div>
    `;
  },

  scholars_list: async () => {
    const data = await API.browse();
    const displayName = s => s.name_zh || s.name_en || s.scholar_id || '';
    const sorted = [...data.scholars].sort((a,b) => displayName(a).localeCompare(displayName(b), 'zh-Hans-CN'));

    // Group by school
    const schoolGroups = {};
    sorted.forEach(s => {
      const school = s.school_id || 'OTHER';
      if (!schoolGroups[school]) schoolGroups[school] = [];
      schoolGroups[school].push(s);
    });

    const schoolOptions = Object.keys(schoolGroups).sort().map(s =>
      `<option value="${escapeHtml(s)}">${escapeHtml(getSchoolName(s))}</option>`
    ).join('');

    let html = `
      <h2><span class="icon">👥</span> 学者名录</h2>
      <div class="filter-bar">
        <label>🔍 学派筛选</label>
        <select id="scholarFilter">
          <option value="ALL">全部显示 (${sorted.length})</option>
          ${schoolOptions}
        </select>
        <span class="hint">点击卡片查看详情</span>
      </div>
      <div class="card-grid" id="scholarGrid">`;

    sorted.forEach(s => {
        const school = s.school_id ? getSchoolName(s.school_id) : '学者';
        const avatarColor = getAvatarColor(s.school_id);
        const name = displayName(s);
        html += `
        <div class="card clickable" data-school="${s.school_id || 'OTHER'}" onclick="window.location.hash='scholar/${escapeHtml(s.scholar_id)}'">
          <div style="display:flex; align-items:center; gap:15px;">
            <div style="width:50px; height:50px; background:${avatarColor}; border-radius:50%; display:flex; align-items:center; justify-content:center; color:#fff; font-size:1.3rem; font-weight:bold; flex-shrink:0;">
              ${escapeHtml(name[0] || '?')}
            </div>
            <div style="overflow:hidden; flex:1;">
              <h3 style="margin:0; font-size:1.1rem; white-space:nowrap; text-overflow:ellipsis; overflow:hidden;">${escapeHtml(name)}</h3>
              <div style="font-size:0.8rem; color:var(--text-secondary);">${escapeHtml(s.name_en)}</div>
              <div class="badge scholar" style="margin-top:6px;">${escapeHtml(school)}</div>
            </div>
          </div>
          ${s.description_zh ? `<p style="margin-top:1rem; font-size:0.85rem;">${escapeHtml(s.description_zh.substring(0, 60))}${s.description_zh.length > 60 ? '...' : ''}</p>` : ''}
        </div>`;
    });
    html += `</div>`;

    // Add filter logic
    setTimeout(() => {
      const filter = document.getElementById('scholarFilter');
      if (filter) {
        filter.addEventListener('change', (e) => {
          const val = e.target.value;
          document.querySelectorAll('#scholarGrid .card').forEach(card => {
            if (val === 'ALL' || card.dataset.school === val) {
              card.style.display = '';
            } else {
              card.style.display = 'none';
            }
          });
        });
      }
    }, 100);

    return html;
  },

  influence: async () => {
    const data = await API.browse();
    const influences = data.influences || [];

    if (influences.length === 0) {
      return `
        <h2><span class="icon">🔗</span> 学术影响网络</h2>
        <div class="empty-state">
          <div class="icon">📭</div>
          <p>暂无影响关系数据</p>
        </div>
      `;
    }

    // Build network data - optimize with scholar lookup map
    const scholarMap = new Map(data.scholars.map(s => [s.scholar_id, s]));
    const nodes = new Map();
    const edges = [];

    // Count incoming edges for node sizing
    const inDegree = new Map();
    influences.forEach(inf => {
      inDegree.set(inf.subject_id, (inDegree.get(inf.subject_id) || 0) + 1);
    });

    influences.forEach(inf => {
      // Add subject node (influenced)
      if (!nodes.has(inf.subject_id)) {
        const scholar = scholarMap.get(inf.subject_id);
        const degree = inDegree.get(inf.subject_id) || 1;
        nodes.set(inf.subject_id, {
          id: inf.subject_id,
          label: scholar ? scholar.name_zh : inf.subject_id,
          color: { background: '#2980b9', border: '#1a5276' },
          size: 12 + Math.min(degree * 3, 20),
          font: { color: getGraphFontColor(), size: 12 }
        });
      }

      // Add object node (influencer)
      if (!nodes.has(inf.object_id)) {
        const scholar = scholarMap.get(inf.object_id);
        nodes.set(inf.object_id, {
          id: inf.object_id,
          label: scholar ? scholar.name_zh : inf.object_id,
          color: { background: '#27ae60', border: '#1e8449' },
          size: 18,
          font: { color: getGraphFontColor(), size: 12 }
        });
      }

      // Add edge (subject was influenced BY object)
      const infYear = parseInt(inf.year, 10);
      edges.push({
        id: `e${edges.length}`,
        from: inf.object_id,
        to: inf.subject_id,
        arrows: { to: { enabled: true, scaleFactor: 0.6 } },
        title: escapeHtml(inf.note_zh),
        color: { color: '#bdc3c7', highlight: '#e74c3c' },
        width: 1.5,
        year: isNaN(infYear) ? null : infYear
      });
    });

    setTimeout(() => {
      const container = document.getElementById('influence-viz');
      if (container) {
        if (typeof vis === 'undefined') {
          container.innerHTML = visFallbackHtml();
          return;
        }
        const graphData = {
          nodes: new vis.DataSet(Array.from(nodes.values())),
          edges: new vis.DataSet(edges)
        };

        const network = new vis.Network(container, graphData, {
          nodes: {
            shape: 'dot',
            borderWidth: 2,
            shadow: { enabled: true, size: 5 },
            font: { face: 'Inter', strokeWidth: 2, strokeColor: '#fff' }
          },
          edges: {
            smooth: { enabled: true, type: 'continuous', roundness: 0.3 }
          },
          physics: {
            enabled: true,
            stabilization: { enabled: true, iterations: 150, fit: true },
            barnesHut: {
              gravitationalConstant: -3500,
              centralGravity: 0.25,
              springLength: 180,
              springConstant: 0.03,
              damping: 0.12,
              avoidOverlap: 0.5
            },
            minVelocity: 0.5,
            maxVelocity: 30
          },
          interaction: {
            hover: true,
            tooltipDelay: 100,
            navigationButtons: true,
            keyboard: true,
            hideEdgesOnDrag: false,
            dragNodes: true,
            dragView: true,
            zoomView: true
          },
          layout: {
            improvedLayout: true,
            randomSeed: 42
          }
        });

        // Keep physics enabled for dynamic interaction
        network.once('stabilizationIterationsDone', () => {
          network.setOptions({ physics: { stabilization: { enabled: false } } });
        });

        // Store network reference for controls
        window.influenceNetwork = network;

        // Click to focus a scholar's neighborhood, double-click to open profile
        makeFocusMode(network, { navigate: (nodeId) => {
          if (String(nodeId).startsWith('SCH_')) window.location.hash = `scholar/${nodeId}`;
        } });

        // Time travel: reveal influence edges up to a chosen year
        const yearsList = [...new Set(edges.map(e => e.year).filter(y => y !== null))].sort((a, b) => a - b);
        const slider = document.getElementById('ttSlider');
        const ttLabel = document.getElementById('ttLabel');
        const ttCount = document.getElementById('ttCount');
        const playBtn = document.getElementById('ttPlay');
        let ttTimer = null;
        const applyYear = (idx) => {
          const y = yearsList[idx];
          graphData.edges.update(edges.map(e => ({ id: e.id, hidden: e.year !== null && e.year > y })));
          const visibleEdges = edges.filter(e => e.year === null || e.year <= y);
          const active = new Set();
          visibleEdges.forEach(e => { active.add(e.from); active.add(e.to); });
          graphData.nodes.update(Array.from(nodes.keys()).map(id => ({ id, hidden: !active.has(id) })));
          if (ttLabel) ttLabel.textContent = y;
          if (ttCount) ttCount.textContent = `截至 ${y} 年 · ${visibleEdges.length} 条影响`;
        };
        const stopPlay = () => {
          if (ttTimer) { clearInterval(ttTimer); ttTimer = null; }
          if (playBtn) playBtn.textContent = '▶️ 时间旅行';
        };
        if (slider && yearsList.length > 1) {
          slider.max = String(yearsList.length - 1);
          slider.value = String(yearsList.length - 1);
          slider.addEventListener('input', () => { stopPlay(); applyYear(+slider.value); });
          if (playBtn) playBtn.addEventListener('click', () => {
            if (ttTimer) { stopPlay(); return; }
            let idx = +slider.value >= yearsList.length - 1 ? 0 : +slider.value;
            playBtn.textContent = '⏸️ 暂停播放';
            applyYear(idx);
            ttTimer = setInterval(() => {
              idx += 1;
              slider.value = String(idx);
              applyYear(idx);
              if (idx >= yearsList.length - 1) stopPlay();
            }, 800);
          });
          const resetBtn = document.getElementById('ttReset');
          if (resetBtn) resetBtn.addEventListener('click', () => {
            stopPlay();
            slider.value = String(yearsList.length - 1);
            applyYear(yearsList.length - 1);
            network.fit();
          });
          applyYear(yearsList.length - 1);
        }
      }
    }, 100);

    return `
      <h2><span class="icon">🔗</span> 学术影响网络</h2>
      <div class="influence-legend">
        <div class="legend-item">
          <div class="legend-dot" style="background:#27ae60;"></div>
          <span>思想来源（被引用者）</span>
        </div>
        <div class="legend-item">
          <div class="legend-dot" style="background:#2980b9;"></div>
          <span>受影响者</span>
        </div>
        <div class="legend-item">
          <span style="margin-left:auto; font-size:0.85rem; color:var(--text-secondary);">
            共 ${influences.length} 条影响关系
          </span>
        </div>
      </div>
      <div class="time-travel">
        <span class="tt-icon" title="拖动滑块观看学术网络随时间生长">🕰️ 时间旅行</span>
        <button class="btn-small" id="ttPlay">▶️ 播放</button>
        <input type="range" id="ttSlider" min="0" max="1" value="1" step="1" aria-label="时间旅行年份滑块" />
        <span class="tt-year" id="ttLabel">—</span>
        <span class="tt-count" id="ttCount"></span>
        <button class="btn-small" id="ttReset">🔄 全部</button>
      </div>
      <div class="physics-controls" style="margin-bottom:1rem; display:flex; gap:0.5rem; flex-wrap:wrap;">
        <button class="primary" onclick="togglePhysics()" id="physicsBtn">⏸️ 暂停动画</button>
        <button class="primary" onclick="shakeNetwork()">🔀 重新排列</button>
        <button class="primary" onclick="window.influenceNetwork && window.influenceNetwork.fit()">📐 自适应</button>
        <span style="color:var(--text-secondary); font-size:0.85rem; margin-left:auto; line-height:2.2;">单击聚焦 · 双击查看详情 · 滚轮缩放</span>
      </div>
      <div id="influence-viz" style="height:700px; position:relative;"></div>

      <h3 style="margin-top:2rem;">影响关系列表</h3>
      <div class="card-grid" style="grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));">
        ${influences.slice(0, 20).map(inf => {
          const subject = data.scholars.find(s => s.scholar_id === inf.subject_id);
          const object = data.scholars.find(s => s.scholar_id === inf.object_id);
          return `
            <div class="card">
              <div style="display:flex; align-items:center; gap:10px; margin-bottom:0.8rem; flex-wrap:wrap;">
                <span style="font-weight:bold; color:var(--accent-color);">${escapeHtml(object ? object.name_zh : inf.object_id)}</span>
                <span style="color:var(--text-secondary);">→</span>
                <span style="font-weight:bold;">${escapeHtml(subject ? subject.name_zh : inf.subject_id)}</span>
                ${inf.year ? `<span class="badge event">${escapeHtml(inf.year)}</span>` : ''}
              </div>
              <p style="font-size:0.9rem;">${escapeHtml(inf.note_zh)}</p>
            </div>
          `;
        }).join('')}
        ${influences.length > 20 ? `<div class="card" style="display:flex;align-items:center;justify-content:center;"><p>还有 ${influences.length - 20} 条关系...</p></div>` : ''}
      </div>
    `;
  },

  timeline: async () => {
    const data = await API.browse();
    const props = [...data.propositions].sort((a, b) => (parseInt(a.year)||0) - (parseInt(b.year)||0));
    const mode = localStorage.getItem('tl_mode') === 'dual' ? 'dual' : 'single';
    const scholarById = new Map((data.scholars || []).map(s => [s.scholar_id, s]));
    const nameOf = (p) => {
      const s = scholarById.get(p.scholar_id);
      return s ? (s.name_zh || s.name_en) : '历史事件';
    };
    const isChinese = (p) => {
      const s = scholarById.get(p.scholar_id);
      const sc = s ? (s.school_id || '') : '';
      return sc.startsWith('SCHOOL_CN') || sc === 'SCHOOL_HK_PRESS';
    };

    let itemsHtml = '';
    props.forEach((p, idx) => {
      const title = nameOf(p);
      const isEvent = !p.scholar_id;
      const dotColor = isEvent ? '#e67e22' : '#2980b9';
      // Cap the stagger so long timelines don't leave far items invisible for seconds
      const delay = Math.min(idx * 50, 1500);

      itemsHtml += `
        <div class="timeline-item" style="animation-delay:${delay}ms;">
          <div class="timeline-dot" style="background:${dotColor}"></div>
          <div class="timeline-year">${escapeHtml(p.year || '—')}</div>
          <div class="timeline-content">
            <h3 style="color:${dotColor}">${escapeHtml(title)}</h3>
            <p>${escapeHtml(p.proposition_text_zh)}</p>
            ${p.concept_ids ? `<div style="margin-top:0.5rem;">${p.concept_ids.split(';').filter(x=>x).map(c => `<span class="badge theory">${escapeHtml(c.trim().replace('CONCEPT_', ''))}</span>`).join(' ')}</div>` : ''}
          </div>
        </div>
      `;
    });

    const west = [];
    const east = [];
    props.forEach(p => (isChinese(p) ? east : west).push(p));
    const dualCol = (list, title, icon) => `
      <div class="tl2-col">
        <div class="tl2-title">${icon} ${title}<span class="tl2-count">${list.length} 条</span></div>
        ${list.map(p => `
          <div class="tl2-item">
            <div class="tl2-top"><span class="badge event">${escapeHtml(p.year || '—')}</span><b>${escapeHtml(nameOf(p))}</b></div>
            <p>${escapeHtml(p.proposition_text_zh)}</p>
          </div>`).join('') || '<div class="tl2-empty">暂无同期记录</div>'}
      </div>`;

    return `
      <h2><span class="icon">⏳</span> 新闻传播思想编年史</h2>
      <div class="view-tabs">
        <button class="tab-btn ${mode === 'single' ? 'active' : ''}" onclick="setTlMode('single')">📜 单栏时间轴</button>
        <button class="tab-btn ${mode === 'dual' ? 'active' : ''}" onclick="setTlMode('dual')">🌏 中西对照</button>
      </div>
      ${mode === 'dual' ? `
        <p style="color:var(--text-secondary); margin-bottom:1rem;">左右两栏按年份排列——看看当弥尔顿论出版自由时，中国的报人在做什么。</p>
        <div class="dual-tl">${dualCol(west, '西方', '🌍')}${dualCol(east, '中国', '🇨🇳')}</div>
      ` : `
        <div style="margin-bottom:1rem; color:var(--text-secondary);">
          共 ${props.length} 个历史节点 ·
          <span class="badge event">橙色 = 历史事件</span>
          <span class="badge scholar">蓝色 = 学者理论</span>
        </div>
        <div class="timeline-container">
          <div class="timeline-line"></div>
          ${itemsHtml}
        </div>
      `}
    `;
  },

  books: async () => {
    const data = await API.browse();
    const books = data.books || [];

    if (books.length === 0) {
      return `
        <h2><span class="icon">📚</span> 经典著作库</h2>
        <div class="empty-state">
          <div class="icon">📭</div>
          <p>暂无著作数据</p>
        </div>
      `;
    }

    const sorted = [...books].sort((a,b) => (parseInt(a.year)||0) - (parseInt(b.year)||0));

    let html = `
      <h2><span class="icon">📚</span> 经典著作库</h2>
      <p style="color:var(--text-secondary); margin-bottom:1.5rem;">共收录 ${books.length} 部改变历史的学术名著</p>
      <div class="card-grid">`;

    sorted.forEach(b => {
      const author = data.scholars.find(s => s.scholar_id === b.scholar_id);
      const authorName = author ? `<a href="#scholar/${escapeHtml(b.scholar_id)}" style="color:var(--accent-color);text-decoration:none;font-weight:500;">${escapeHtml(author.name_zh)}</a>` : '未知作者';

      html += `
        <div class="card">
          <div style="font-size:0.8rem; color:var(--text-secondary); margin-bottom:0.5rem;">📅 ${escapeHtml(b.year)}</div>
          <h3 style="margin-bottom:0.3rem;">《${escapeHtml(b.title_zh)}》</h3>
          <div style="font-size:0.85rem; color:var(--text-secondary); font-style:italic; margin-bottom:1rem;">${escapeHtml(b.title_en)}</div>
          <div style="margin-bottom:0.8rem;">👤 ${authorName}</div>
          <p>${escapeHtml(b.description_zh)}</p>
        </div>`;
    });
    html += '</div>';
    return html;
  },

  concepts: async () => {
    const data = await API.browse();
    const concepts = data.concepts || [];

    if (concepts.length === 0) {
      return `
        <h2><span class="icon">💡</span> 核心概念</h2>
        <div class="empty-state">
          <div class="icon">📭</div>
          <p>暂无概念数据</p>
        </div>
      `;
    }

    const propsByConcept = {};
    (data.propositions || []).forEach(p => {
      const cids = (p.concept_ids || '').split(';').filter(x => x.trim());
      cids.forEach(cid => {
        const id = cid.trim();
        if (!propsByConcept[id]) propsByConcept[id] = [];
        propsByConcept[id].push(p);
      });
    });

    const scholarMap = new Map(data.scholars.map(s => [s.scholar_id, s]));
    const mode = localStorage.getItem('concept_tab') === 'net' ? 'net' : 'cards';

    let cardsHtml = '';
    concepts.forEach(c => {
      const related = propsByConcept[c.concept_id] || [];
      cardsHtml += `
        <div class="card">
          <h3>${escapeHtml(c.name_zh)}</h3>
          <div class="badge theory" style="margin-bottom:0.8rem;">${escapeHtml(c.concept_id.replace('CONCEPT_', ''))}</div>
          <p>${escapeHtml(c.description_zh)}</p>
          ${related.length > 0 ? `
            <div style="margin-top:1rem; padding-top:0.8rem; border-top:1px solid var(--border-color);">
              <div style="font-size:0.8rem; font-weight:600; color:var(--text-secondary); margin-bottom:0.5rem;">相关命题 (${related.length})</div>
              ${related.slice(0, 3).map(p => {
                const scholar = scholarMap.get(p.scholar_id);
                return `<div style="font-size:0.85rem; margin-bottom:0.3rem; color:var(--text-secondary);">
                  <span class="badge event">${escapeHtml(p.year)}</span>
                  ${scholar ? `<a href="#scholar/${escapeHtml(p.scholar_id)}" style="color:var(--accent-color);text-decoration:none;">${escapeHtml(scholar.name_zh)}</a>` : ''}
                </div>`;
              }).join('')}
              ${related.length > 3 ? `<div style="font-size:0.8rem; color:var(--text-secondary);">...还有 ${related.length - 3} 条</div>` : ''}
            </div>
          ` : ''}
        </div>`;
    });

    setTimeout(() => {
      const el = document.getElementById('concept-net');
      if (!el) return;
      if (typeof vis === 'undefined') { el.innerHTML = visFallbackHtml(); return; }
      // Concept co-occurrence from propositions' concept_ids
      const validIds = new Set(concepts.map(c => c.concept_id));
      const cooc = new Map();
      (data.propositions || []).forEach(p => {
        const cids = (p.concept_ids || '').split(';').map(x => x.trim()).filter(x => validIds.has(x));
        for (let i = 0; i < cids.length; i++) {
          for (let j = i + 1; j < cids.length; j++) {
            const key = [cids[i], cids[j]].sort().join('|');
            cooc.set(key, (cooc.get(key) || 0) + 1);
          }
        }
      });
      if (!cooc.size) {
        el.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-secondary);">暂无概念共现关系</div>';
        return;
      }
      const netNodes = concepts
        .filter(c => [...cooc.keys()].some(k => k.split('|').includes(c.concept_id)))
        .map(c => ({
          id: c.concept_id,
          label: c.name_zh,
          title: escapeHtml(c.description_zh || c.name_zh),
          shape: 'dot',
          size: 10 + Math.min((propsByConcept[c.concept_id] || []).length * 4, 26),
          color: { background: '#8e7cc3', border: '#674ea7', highlight: { background: '#e74c3c', border: '#fff' } },
          font: { color: getGraphFontColor(), face: 'Inter', size: 13, strokeWidth: 2, strokeColor: document.documentElement.getAttribute('data-theme') === 'dark' ? '#16213e' : '#fff' }
        }));
      const netEdges = [...cooc.entries()].map(([key, count], i) => {
        const [a, b] = key.split('|');
        return {
          id: `c${i}`,
          from: a,
          to: b,
          width: 1 + count,
          title: `共同出现在 ${count} 条命题中`,
          color: { color: '#b3a6d6', highlight: '#e74c3c' }
        };
      });
      const net = new vis.Network(el, {
        nodes: new vis.DataSet(netNodes),
        edges: new vis.DataSet(netEdges)
      }, {
        nodes: { shape: 'dot' },
        physics: { barnesHut: { gravitationalConstant: -3200, springLength: 150 }, stabilization: { iterations: 120, fit: true } },
        interaction: { hover: true, tooltipDelay: 120 }
      });
      makeFocusMode(net, { navigate: null });
    }, 100);

    return `
      <h2><span class="icon">💡</span> 核心概念</h2>
      <div class="view-tabs">
        <button class="tab-btn ${mode === 'cards' ? 'active' : ''}" onclick="setConceptTab('cards')">🗂️ 卡片</button>
        <button class="tab-btn ${mode === 'net' ? 'active' : ''}" onclick="setConceptTab('net')">🕸️ 关联网络</button>
      </div>
      <p style="color:var(--text-secondary); margin-bottom:1.5rem;">共收录 ${concepts.length} 个传播学核心概念${mode === 'net' ? ' · 连线表示两个概念共同出现在同一命题中 · 单击概念聚焦' : ''}</p>
      ${mode === 'net'
        ? '<div id="concept-net" style="height:620px; background:var(--card-bg); border:1px solid var(--border-color); border-radius:12px; position:relative;"></div>'
        : `<div class="card-grid">${cardsHtml}</div>`}
    `;
  },

  scholar: async (id) => {
    const data = await API.browse();
    const scholar = data.scholars.find(s => s.scholar_id === id);
    if (!scholar) return `<div class="card" style="border-color:#e74c3c;"><h3>未找到该学者</h3><p>Scholar not found: ${escapeHtml(id)}</p></div>`;

    const props = data.propositions.filter(p => p.scholar_id === id).sort((a, b) => (parseInt(a.year)||0) - (parseInt(b.year)||0));
    const books = (data.books || []).filter(b => b.scholar_id === id).sort((a, b) => (parseInt(a.year)||0) - (parseInt(b.year)||0));
    const influences = (data.influences || []).filter(i => i.subject_id === id || i.object_id === id);
    const rels = (data.relations || []).filter(r => r.subject_id === id || r.object_id === id);

    // Influence section
    let influenceHtml = '';
    if (influences.length > 0) {
      influenceHtml = `
        <h3 style="border-bottom:2px solid var(--accent-light); padding-bottom:0.5rem; margin-top:2rem;">🔗 学术影响关系</h3>
        <div class="card-grid" style="grid-template-columns:1fr;">`;

      influences.forEach(inf => {
        const isSubject = inf.subject_id === id;
        const otherId = isSubject ? inf.object_id : inf.subject_id;
        const other = data.scholars.find(s => s.scholar_id === otherId);
        const direction = isSubject ? '受影响于' : '影响了';

        influenceHtml += `
          <div class="card" style="padding:1rem;">
            <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
              <span style="font-weight:bold;">${direction}</span>
              <a href="#scholar/${escapeHtml(otherId)}" style="color:var(--accent-color); text-decoration:none; font-weight:bold;">${escapeHtml(other ? other.name_zh : otherId)}</a>
              ${inf.year ? `<span class="badge event">${escapeHtml(inf.year)}</span>` : ''}
            </div>
            <p style="margin-top:0.5rem; font-size:0.9rem;">${escapeHtml(inf.note_zh)}</p>
          </div>`;
      });
      influenceHtml += '</div>';
    }

    // Books section
    let bookHtml = '';
    if (books.length > 0) {
      bookHtml = `
        <h3 style="border-bottom:2px solid var(--accent-light); padding-bottom:0.5rem; margin-top:2rem;">📚 代表著作</h3>
        <div class="card-grid" style="grid-template-columns:1fr 1fr; gap:1rem;">`;
      books.forEach(b => {
        bookHtml += `
          <div class="card" style="padding:1rem;">
            <div style="font-weight:bold;">《${escapeHtml(b.title_zh)}》 (${escapeHtml(b.year)})</div>
            <div style="font-size:0.8rem; color:var(--text-secondary); margin-top:0.3rem;">${escapeHtml(b.description_zh)}</div>
          </div>`;
      });
      bookHtml += '</div>';
    }

    // Social relations
    let socialHtml = '';
    if (rels.length > 0) {
      socialHtml = `
        <h3 style="border-bottom:2px solid var(--accent-light); padding-bottom:0.5rem; margin-top:2rem;">👥 社会关系</h3>
        <div id="social-viz" style="height:400px;"></div>`;

      setTimeout(() => {
        const container = document.getElementById('social-viz');
        if (container) {
          if (typeof vis === 'undefined') {
            container.innerHTML = visFallbackHtml();
            return;
          }
          const sNodes = new Map();
          const sEdges = [];
          sNodes.set(id, { id, label: scholar.name_zh, color: '#e74c3c', size: 30 });

          rels.forEach(r => {
            [r.subject_id, r.object_id].forEach(nid => {
              if (!sNodes.has(nid)) {
                const s = data.scholars.find(x => x.scholar_id === nid);
                sNodes.set(nid, { id: nid, label: s ? s.name_zh : nid, color: '#2c3e50', size: 20 });
              }
            });
            sEdges.push({
              from: r.subject_id,
              to: r.object_id,
              label: escapeHtml(r.relation),
              font: { size: 11, align: 'middle', color: getGraphFontColor() },
              arrows: 'to',
              color: { color: '#bdc3c7' }
            });
          });

          new vis.Network(container, {
            nodes: new vis.DataSet(Array.from(sNodes.values())),
            edges: new vis.DataSet(sEdges)
          }, {
            nodes: { shape: 'dot', font: { face: 'Inter', color: getGraphFontColor() } },
            physics: { stabilization: false, barnesHut: { gravitationalConstant: -2000 } }
          });
        }
      }, 100);
    }

    // Props timeline
    let timelineHtml = '';
    if (props.length === 0) {
      timelineHtml = '<p style="color:var(--text-secondary); font-style:italic;">暂无收录命题。</p>';
    } else {
      props.forEach(p => {
        const concepts = (p.concept_ids || '').split(';').filter(x=>x).map(c => `<span class="badge theory">${escapeHtml(c.trim().replace('CONCEPT_', ''))}</span>`).join(' ');
        timelineHtml += `
          <div class="card" style="margin-bottom:1rem; border-left:4px solid var(--accent-color);">
            <div style="font-weight:bold; color:var(--accent-color);">${escapeHtml(p.year || '—')}</div>
            <p style="margin:0.5rem 0;">${escapeHtml(p.proposition_text_zh)}</p>
            <div>${concepts}</div>
          </div>`;
      });
    }

    const avatarColor = getAvatarColor(scholar.school_id);
    const scholarName = scholar.name_zh || scholar.name_en || scholar.scholar_id;
    const quote = (data.quotes || []).find(q => q.scholar_id === id);

    AchievementManager.visitScholar(id, data);

    return `
      <button onclick="history.back()" class="back-btn">← 返回</button>

      <div class="scholar-profile">
        <div>
          <div class="card" style="text-align:center;">
            <div class="scholar-avatar" style="background:${avatarColor};">${escapeHtml(scholarName[0] || '?')}</div>
            <h2 style="border:none; margin-bottom:0.5rem; justify-content:center;">${escapeHtml(scholarName)}</h2>
            <div style="color:var(--text-secondary); font-size:0.9rem; margin-bottom:1rem;">${escapeHtml(scholar.name_en)}</div>
            <div class="badge scholar" style="margin-bottom:1rem;">${escapeHtml(scholar.school_id ? getSchoolName(scholar.school_id) : '学者')}</div>
            <p style="text-align:left; font-size:0.9rem; line-height:1.6;">${escapeHtml(scholar.description_zh) || '暂无简介'}</p>
            ${scholar.active_year ? `<div style="margin-top:1rem; font-size:0.8rem; color:var(--text-secondary);">活跃年份: ${escapeHtml(scholar.active_year)}</div>` : ''}
            ${quote ? `
              <div class="quote-card">
                <div class="quote-mark">❝</div>
                <div class="quote-text">${escapeHtml(quote.quote_zh)}</div>
                ${quote.context_zh ? `<div class="quote-ctx">—— ${escapeHtml(quote.context_zh)}</div>` : ''}
              </div>` : ''}
          </div>
        </div>

        <div>
          ${influenceHtml}
          ${socialHtml}
          ${bookHtml}
          <h3 style="border-bottom:2px solid var(--accent-light); padding-bottom:0.5rem; margin-top:2rem;">📜 学术编年</h3>
          ${timelineHtml}
        </div>
      </div>
    `;
  },

  map: async () => {
    const data = await API.browse();

    const groups = {};
    data.scholars.forEach(s => {
      if (!s.school_id) return;
      if (!groups[s.school_id]) groups[s.school_id] = [];
      let y = parseInt(s.active_year);
      if (isNaN(y)) return;
      groups[s.school_id].push({ ...s, year: y });
    });

    const schoolKeys = Object.keys(groups).sort((a,b) => {
      const avgA = groups[a].reduce((sum, s) => sum + s.year, 0) / groups[a].length;
      const avgB = groups[b].reduce((sum, s) => sum + s.year, 0) / groups[b].length;
      return avgA - avgB;
    });

    const totalYears = 2030 - 1600;
    let pxPerYear = 2.5;
    let chartWidth = totalYears * pxPerYear + 200;
    const startYear = 1600;

    let rowsHtml = '';
    schoolKeys.forEach(sid => {
      const scholars = groups[sid];
      const schoolName = getSchoolName(sid);

      let dots = '';
      scholars.forEach(s => {
        const left = (s.year - startYear) * pxPerYear;
        const dotTitle = `${s.name_zh} (${s.active_year})`;
        dots += `<div class="map-dot" onclick="window.location.hash='scholar/${escapeHtml(s.scholar_id)}'" title="${escapeHtml(dotTitle)}" style="left:${left}px;"><div class="dot-label">${escapeHtml(s.name_zh)}</div></div>`;
      });

      rowsHtml += `
        <div class="map-row">
          <div class="map-school-label">${escapeHtml(schoolName)}</div>
          <div class="map-track">${dots}</div>
        </div>`;
    });

    let axisHtml = '';
    for (let y = startYear; y <= 2030; y += 25) {
      const left = (y - startYear) * pxPerYear;
      axisHtml += `<div class="map-axis-tick" style="left:${left}px;">${y}</div>`;
    }

    return `
      <style>
        .map-container {
          overflow-x: scroll !important;
          overflow-y: auto !important;
          border: 1px solid var(--border-color);
          background: var(--card-bg);
          border-radius: 12px;
          height: 750px;
          -webkit-overflow-scrolling: touch;
          cursor: grab;
        }
        .map-container:active { cursor: grabbing; }
        .map-inner { width: ${chartWidth}px; min-width: ${chartWidth}px; position: relative; padding-left: 180px; padding-top: 40px; padding-bottom: 20px; }
        .map-row { display: flex; border-bottom: 1px solid var(--border-color); height: 50px; align-items: center; position: relative; transition: background 0.2s; }
        .map-row:hover { background: var(--accent-light); }
        .map-school-label { position: sticky; left: 0; width: 170px; min-width: 170px; text-align: right; padding-right: 15px; font-size: 0.8rem; font-weight: 600; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; background: var(--card-bg); z-index: 10; height: 50px; line-height: 50px; border-right: 1px solid var(--border-color); }
        .map-track { position: relative; flex: 1; height: 100%; min-width: ${chartWidth - 180}px; }
        .map-dot { position: absolute; top: 50%; transform: translateY(-50%); width: 12px; height: 12px; background: var(--accent-color); border-radius: 50%; cursor: pointer; transition: all 0.2s; border: 2px solid var(--card-bg); box-shadow: var(--shadow-sm); }
        .map-dot:hover { transform: translateY(-50%) scale(1.5); z-index: 100; background: #e74c3c; }
        .dot-label { position: absolute; top: -24px; left: 50%; transform: translateX(-50%); font-size: 10px; white-space: nowrap; background: var(--text-primary); color: var(--bg-color); padding: 2px 6px; border-radius: 4px; display: none; pointer-events: none; }
        .map-dot:hover .dot-label { display: block; }
        .map-axis { position: sticky; top: 0; left: 180px; width: ${chartWidth}px; height: 30px; border-bottom: 2px solid var(--border-color); background: var(--card-bg); z-index: 5; }
        .map-axis-tick { position: absolute; bottom: 0; font-size: 10px; color: var(--text-secondary); border-left: 1px solid var(--border-color); padding-left: 4px; height: 10px; }
      </style>

      <h2><span class="icon">🗺️</span> 学术全景图</h2>
      <p style="color:var(--text-secondary); margin-bottom:1rem;">纵轴：学派 (按年代排序) · 横轴：活跃年份 · 左右拖动滚动 · 点击圆点查看详情</p>

      <div class="map-controls" style="margin-bottom:1rem; display:flex; gap:0.5rem; align-items:center;">
        <button class="btn-small" onclick="mapZoom('out')" title="缩小">➖ 缩小</button>
        <button class="btn-small" onclick="mapZoom('reset')" title="重置">🔄 重置</button>
        <button class="btn-small" onclick="mapZoom('in')" title="放大">➕ 放大</button>
        <span id="zoomLevel" style="margin-left:1rem; font-size:0.85rem; color:var(--text-secondary);">缩放: 100%</span>
      </div>

      <div class="map-container" id="mapContainer">
        <div class="map-inner">
          <div class="map-axis">${axisHtml}</div>
          ${rowsHtml}
        </div>
      </div>
    `;
  },

  graph: async () => {
    const data = await API.browse();

    const schools = new Set();
    data.scholars.forEach(s => {
      if(s.school_id) schools.add(s.school_id);
    });
    const sortedSchools = Array.from(schools).sort();

    let filterHtml = `
      <div class="filter-bar">
        <label>🔍 学派筛选</label>
        <select id="schoolFilter">
          <option value="ALL">全部显示</option>
          ${sortedSchools.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(getSchoolName(s))}</option>`).join('')}
        </select>
        <span class="hint">拖动节点调整布局 · 单击聚焦 · 双击查看详情</span>
      </div>`;

    // Build nodes - scholars only for initial performance
    const nodes = [];
    const edges = [];
    const nodeIds = new Set();

    data.scholars.forEach(s => {
      if(s.scholar_id) {
        nodes.push({
          id: s.scholar_id,
          label: s.name_zh || s.name_en || s.scholar_id,
          group: 'scholar',
          school: s.school_id,
          shape: 'dot',
          size: 28,
          color: { background: getAvatarColor(s.school_id), border: '#fff', highlight: { background: '#e74c3c', border: '#fff' } },
          font: { color: getGraphFontColor(), size: 14, face: 'Inter', strokeWidth: 3, strokeColor: document.documentElement.getAttribute('data-theme') === 'dark' ? '#16213e' : '#fff' }
        });
        nodeIds.add(s.scholar_id);
      }
    });

    // Add influence edges between scholars
    const influences = data.influences || [];
    influences.forEach(inf => {
      if (nodeIds.has(inf.subject_id) && nodeIds.has(inf.object_id)) {
        edges.push({
          from: inf.object_id,
          to: inf.subject_id,
          arrows: { to: { enabled: true, scaleFactor: 0.5 } },
          color: { color: '#3498db', highlight: '#e74c3c' },
          width: 1.5,
          title: escapeHtml(inf.note_zh)
        });
      }
    });

    setTimeout(() => {
      const container = document.getElementById('graph-viz');
      if(container) {
        // Check if vis.js is loaded
        if (typeof vis === 'undefined') {
          container.innerHTML = visFallbackHtml();
          return;
        }

        // Show loading state
        container.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-secondary);"><div class="spinner"></div><p>正在加载图谱...</p></div>';

        if (nodes.length === 0) {
          container.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-secondary);"><p>⚠️ 没有学者数据可显示</p></div>';
          return;
        }

        try {
          const graphData = { nodes: new vis.DataSet(nodes), edges: new vis.DataSet(edges) };
          const network = new vis.Network(container, graphData, {
          nodes: { font: { face: 'Inter' } },
          physics: {
            enabled: true,
            stabilization: { enabled: true, iterations: 120, fit: true },
            barnesHut: {
              gravitationalConstant: -2500,
              centralGravity: 0.2,
              springLength: 150,
              springConstant: 0.03,
              damping: 0.15,
              avoidOverlap: 0.5
            },
            minVelocity: 0.5,
            maxVelocity: 30
          },
          interaction: {
            hover: true,
            tooltipDelay: 100,
            navigationButtons: true,
            hideEdgesOnDrag: false,
            dragNodes: true,
            dragView: true,
            zoomView: true
          },
          layout: {
            improvedLayout: true,
            randomSeed: 42
          }
        });

        network.once('stabilizationIterationsDone', () => {
          network.setOptions({ physics: { stabilization: { enabled: false } } });
        });

        window.graphNetwork = network;

        // Click to focus a scholar's neighborhood, double-click to open profile
        makeFocusMode(network, { navigate: (nodeId) => {
          if (String(nodeId).startsWith('SCH_')) window.location.hash = `scholar/${nodeId}`;
        } });

        document.getElementById('schoolFilter').addEventListener('change', (e) => {
          const val = e.target.value;
          const allNodes = graphData.nodes.get();

          if (val === 'ALL') {
            graphData.nodes.update(allNodes.map(n => ({
              id: n.id,
              hidden: false
            })));
          } else {
            graphData.nodes.update(allNodes.map(n => ({
              id: n.id,
              hidden: n.school !== val
            })));
          }

          network.fit();
        });
        } catch (err) {
          console.error('Graph render error:', err);
          container.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-secondary);"><p>⚠️ 图谱渲染失败: ' + escapeHtml(err.message) + '</p><button class="primary" onclick="location.reload()">🔄 刷新</button></div>';
        }
      }
    }, 200);

    return `
      <h2><span class="icon">🕸️</span> 知识关系图谱</h2>
      ${filterHtml}
      <div class="physics-controls" style="margin-bottom:1rem; display:flex; gap:0.5rem; flex-wrap:wrap;">
        <button class="primary" onclick="togglePhysics()" id="physicsBtn">⏸️ 暂停动画</button>
        <button class="primary" onclick="shakeNetwork()">🔀 重新排列</button>
        <button class="primary" onclick="window.graphNetwork && window.graphNetwork.fit()">📐 自适应</button>
        <span style="color:var(--text-secondary); font-size:0.85rem; margin-left:auto; line-height:2.2;">拖动节点 · 滚轮缩放 · 点击查看详情</span>
      </div>
      <div id="graph-viz"></div>
    `;
  },

  data: async () => {
    if (IS_STATIC) {
      return `
        <h2><span class="icon">📝</span> 数据录入</h2>
        <div class="card">
          <h3>🔒 只读模式</h3>
          <p>当前为静态部署，请在本地编辑 CSV 文件后重新构建。</p>
        </div>
      `;
    }
    const list = await API.list_csv();
    const options = list.files.map(f => `<option value="${escapeHtml(f.name)}">${escapeHtml(f.name)}</option>`).join('');
    return `
      <h2><span class="icon">📝</span> 数据录入</h2>
      <div class="action-bar">
        <select id="csvSelect" style="padding:8px 12px; border-radius:8px; border:1px solid var(--border-color); background:var(--bg-color); color:var(--text-primary);">${options}</select>
        <button class="primary" onclick="loadCsv()">📂 加载</button>
        <button class="primary" onclick="saveCsv()">💾 保存</button>
      </div>
      <textarea id="csvEditor" class="csv-editor" placeholder="选择 CSV 文件后点击加载..."></textarea>
    `;
  },

  validate: async () => {
    if (IS_STATIC) {
      return `
        <h2><span class="icon">✅</span> 校验工具</h2>
        <div class="card">
          <h3>✅ 预校验通过</h3>
          <p>静态构建意味着数据已通过校验。</p>
        </div>
      `;
    }

    const result = await API.validate();
    const errorCount = (result.summary && result.summary.errors) || 0;
    const warnCount = (result.summary && result.summary.warnings) || 0;
    const hasErrors = errorCount > 0;

    return `
      <h2><span class="icon">✅</span> 校验工具</h2>
      <div class="stats-grid" style="grid-template-columns: repeat(2, 1fr);">
        <div class="stat-card" style="border-color: ${hasErrors ? '#e74c3c' : '#27ae60'};">
          <div class="stat-value" style="color: ${hasErrors ? '#e74c3c' : '#27ae60'};">${errorCount}</div>
          <div class="stat-label">错误</div>
        </div>
        <div class="stat-card">
          <div class="stat-value" style="color: #f39c12;">${warnCount}</div>
          <div class="stat-label">警告</div>
        </div>
      </div>
      ${result.errors.length > 0 ? `
        <h3>错误详情</h3>
        <div class="card-grid" style="grid-template-columns:1fr;">
          ${result.errors.map(e => `
            <div class="card" style="border-left:4px solid #e74c3c;">
              <div style="font-weight:bold; color:#e74c3c;">${escapeHtml(e.code)} ${escapeHtml(e.friendly || '')}</div>
              <p>${escapeHtml(e.message)}</p>
              <p style="font-size:0.8rem; margin-top:0.4rem;">${escapeHtml(Object.entries(e.context || {}).map(([k, v]) => `${k}=${v}`).join(' · '))}</p>
            </div>
          `).join('')}
        </div>
      ` : '<div class="card" style="background:#d4edda; border-color:#28a745;"><h3 style="color:#155724;">✅ 所有检查通过</h3></div>'}
    `;
  },

  browse: async () => {
    const data = await API.browse();
    return `
      <h2><span class="icon">📖</span> 证据库</h2>
      <p style="color:var(--text-secondary); margin-bottom:1.5rem;">共收录 ${data.passages.length} 条可追溯的文献证据</p>
      <div class="card-grid">
        ${data.passages.map(p => `
          <div class="card">
            <div class="meta">
              <span class="badge event">${escapeHtml(p.published_year || '年份不详')}</span>
              <span>${escapeHtml(p.source_type || 'unknown')}</span>
            </div>
            <h3 style="font-size:1rem;">${escapeHtml(p.source_title)}</h3>
            <p style="font-style:italic; border-left:3px solid var(--accent-color); padding-left:1rem; margin:1rem 0;">"${escapeHtml(p.passage_text)}"</p>
            ${p.source_url ? `<a href="${escapeHtml(p.source_url)}" target="_blank" rel="noopener noreferrer" style="font-size:0.8rem; color:var(--accent-color);">🔗 ${escapeHtml(p.source_title)}</a>` : ''}
            <div style="font-size:0.8rem; color:var(--text-secondary); margin-top:0.5rem;">📍 ${escapeHtml(p.locator) || '位置不详'}</div>
          </div>
        `).join('')}
      </div>
    `;
  },

  search: async (query) => {
    AchievementManager.searched();
    const results = await API.search(query);
    const total = results.scholars.length + results.books.length + results.propositions.length;

    return `
      <h2><span class="icon">🔍</span> 搜索结果: "${escapeHtml(query)}"</h2>
      <p style="color:var(--text-secondary); margin-bottom:1.5rem;">共找到 ${total} 条结果</p>

      ${results.scholars.length > 0 ? `
        <h3>👥 学者 (${results.scholars.length})</h3>
        <div class="card-grid" style="margin-bottom:2rem;">
          ${results.scholars.slice(0, 10).map(s => `
            <div class="card clickable" onclick="window.location.hash='scholar/${escapeHtml(s.scholar_id)}'">
              <h3>${escapeHtml(s.name_zh || s.name_en || s.scholar_id)}</h3>
              <p>${escapeHtml(s.description_zh)}</p>
            </div>
          `).join('')}
        </div>
      ` : ''}

      ${results.books.length > 0 ? `
        <h3>📚 著作 (${results.books.length})</h3>
        <div class="card-grid" style="margin-bottom:2rem;">
          ${results.books.slice(0, 10).map(b => `
            <div class="card">
              <h3>《${escapeHtml(b.title_zh || b.title_en || '')}》</h3>
              <p>${escapeHtml(b.description_zh)}</p>
            </div>
          `).join('')}
        </div>
      ` : ''}

      ${results.propositions.length > 0 ? `
        <h3>📜 命题 (${results.propositions.length})</h3>
        <div class="card-grid">
          ${results.propositions.slice(0, 10).map(p => `
            <div class="card">
              <div class="badge event">${escapeHtml(p.year || '—')}</div>
              <p style="margin-top:0.5rem;">${escapeHtml(p.proposition_text_zh)}</p>
            </div>
          `).join('')}
        </div>
      ` : ''}

      ${total === 0 ? '<div class="empty-state"><div class="icon">🔍</div><p>未找到匹配结果</p></div>' : ''}
    `;
  },

  // ===== 主题导览 =====
  tours: async () => {
    const done = AchievementManager.state ? AchievementManager.state.toursDone : [];
    return `
      <h2><span class="icon">🎫</span> 主题导览</h2>
      <p style="color:var(--text-secondary); margin-bottom:1.5rem;">像逛博物馆一样，跟着策展路线一站一站地读。</p>
      <div class="card-grid">
        ${TOURS.map(t => {
          const isDone = done.includes(t.id);
          return `
          <div class="card clickable" onclick="window.location.hash='tour/${t.id}'">
            <h3>${t.icon} ${escapeHtml(t.title)}</h3>
            <p>${escapeHtml(t.intro.length > 80 ? t.intro.slice(0, 80) + '…' : t.intro)}</p>
            <div class="badge ${isDone ? 'scholar' : 'event'}" style="margin-top:0.8rem;">${isDone ? '✓ 已完成' : `${t.steps.length} 站`}</div>
          </div>`;
        }).join('')}
      </div>
    `;
  },

  tour: async (id) => {
    const t = TOURS.find(x => x.id === id);
    if (!t) return `<div class="card" style="border-color:#e74c3c;"><h3>未找到该导览</h3><p>Tour not found: ${escapeHtml(id || '')}</p></div>`;
    window.__tour = { id, i: 0 };
    setTimeout(() => tourGo(0), 0);
    return `
      <button onclick="history.back()" class="back-btn">← 返回导览列表</button>
      <h2>${t.icon} ${escapeHtml(t.title)}</h2>
      <p style="color:var(--text-secondary); margin-bottom:1rem;">${escapeHtml(t.intro)}</p>
      <div class="tour-dots">${t.steps.map((_, i) => `<span class="tour-dot" id="td${i}"></span>`).join('')}</div>
      <div id="tourStep"><div class="loading"><div class="spinner"></div></div></div>
      <div class="tour-nav">
        <button class="secondary" id="tourPrev" onclick="tourGo(-1)">← 上一站</button>
        <span style="color:var(--text-secondary);" id="tourPos"></span>
        <button class="primary" id="tourNext" onclick="tourGo(1)">下一站 →</button>
      </div>
    `;
  },

  // ===== 辩论场 =====
  debates: async () => `
    <h2><span class="icon">⚔️</span> 辩论场</h2>
    <p style="color:var(--text-secondary); margin-bottom:1.5rem;">思想史上的经典交锋——左右两端，各自成理。</p>
    <div class="card-grid">
      ${DEBATES.map(d => `
        <div class="card clickable" onclick="window.location.hash='debate/${d.id}'">
          <h3>${d.icon} ${escapeHtml(d.title)}</h3>
          <p>${escapeHtml(d.context.length > 80 ? d.context.slice(0, 80) + '…' : d.context)}</p>
          <div class="badge event" style="margin-top:0.8rem;">${escapeHtml(d.period)}</div>
        </div>
      `).join('')}
    </div>
  `,

  debate: async (id) => {
    const d = DEBATES.find(x => x.id === id);
    if (!d) return `<div class="card" style="border-color:#e74c3c;"><h3>未找到该辩论</h3></div>`;
    const data = await API.browse();
    const panel = (side) => {
      const s = (data.scholars || []).find(x => x.scholar_id === side.scholar_id);
      if (!s) return '<div class="deba-panel"></div>';
      const props = (data.propositions || []).filter(p => p.scholar_id === s.scholar_id).slice(0, 3);
      const books = (data.books || []).filter(b => b.scholar_id === s.scholar_id).slice(0, 2);
      const quote = (data.quotes || []).find(q => q.scholar_id === s.scholar_id);
      const name = s.name_zh || s.name_en || s.scholar_id;
      return `
        <div class="deba-panel">
          <div class="scholar-avatar" style="background:${getAvatarColor(s.school_id)}; width:56px; height:56px; font-size:1.4rem; margin:0 auto 0.8rem;">${escapeHtml((name || '?')[0])}</div>
          <h3 style="margin-bottom:0.2rem;"><a href="#scholar/${escapeHtml(s.scholar_id)}" style="color:inherit; text-decoration:none;">${escapeHtml(name)}</a></h3>
          <div class="badge scholar" style="margin-bottom:0.8rem;">${escapeHtml(s.school_id ? getSchoolName(s.school_id) : '学者')}</div>
          <div class="deba-stance">💬 ${escapeHtml(side.stance)}</div>
          ${props.length ? `<div class="deba-list"><b>相关命题</b>${props.map(p => `<div>· ${escapeHtml(p.proposition_text_zh)}</div>`).join('')}</div>` : ''}
          ${books.length ? `<div class="deba-list"><b>代表著作</b>${books.map(b => `<div>· 《${escapeHtml(b.title_zh)}》(${escapeHtml(b.year)})</div>`).join('')}</div>` : ''}
          ${quote ? `<div class="quote-card small"><div class="quote-text">${escapeHtml(quote.quote_zh)}</div><div class="quote-ctx">—— ${escapeHtml(quote.context_zh || '')}</div></div>` : ''}
        </div>
      `;
    };
    return `
      <button onclick="history.back()" class="back-btn">← 返回辩论场</button>
      <h2>${d.icon} ${escapeHtml(d.title)}</h2>
      <div class="badge event" style="margin-bottom:1rem;">${escapeHtml(d.period)}</div>
      <p style="color:var(--text-secondary); line-height:1.8; margin-bottom:1.5rem;">${escapeHtml(d.context)}</p>
      <div class="deba-arena">${panel(d.left)}<div class="deba-vs">VS</div>${panel(d.right)}</div>
      <p style="color:var(--text-secondary); font-size:0.85rem; margin-top:1rem;">💡 点击姓名可查看学者完整主页与证据段落。</p>
    `;
  },

  // ===== 知识竞答 =====
  quiz: async () => {
    const data = await API.browse();
    window.Quiz = { questions: buildQuizQuestions(data), index: 0, score: 0, answered: false };
    const best = AchievementManager.state ? AchievementManager.state.quizBest : 0;
    return `
      <h2><span class="icon">🧠</span> 知识竞答</h2>
      <div class="quiz-meta-bar">
        <span>每轮 10 题 · 全部出自本馆数据</span>
        <span>历史最佳：<b>${best}</b> 分</span>
      </div>
      ${window.Quiz.questions.length ? `<div id="quizBody">${quizQuestionHtml()}</div>` : '<div class="card"><p>题库数据不足，无法生成题目。</p></div>'}
    `;
  },

  // ===== 连接游戏 =====
  game: async () => {
    const data = await API.browse();
    const scholarIds = new Set((data.scholars || []).map(s => s.scholar_id));
    const adj = new Map();
    (data.influences || []).forEach(inf => {
      if (!inf.subject_id || !inf.object_id) return;
      if (!scholarIds.has(inf.subject_id) || !scholarIds.has(inf.object_id)) return;
      if (!adj.has(inf.subject_id)) adj.set(inf.subject_id, new Set());
      if (!adj.has(inf.object_id)) adj.set(inf.object_id, new Set());
      adj.get(inf.subject_id).add(inf.object_id);
      adj.get(inf.object_id).add(inf.subject_id);
    });
    const ids = [...adj.keys()];
    const bfs = (a, b) => {
      const prev = new Map([[a, null]]);
      const queue = [a];
      while (queue.length) {
        const cur = queue.shift();
        if (cur === b) break;
        (adj.get(cur) || new Set()).forEach(n => {
          if (!prev.has(n)) { prev.set(n, cur); queue.push(n); }
        });
      }
      if (!prev.has(b)) return null;
      const path = [];
      let cur = b;
      while (cur !== null && cur !== undefined) { path.unshift(cur); cur = prev.get(cur); }
      return path;
    };
    const pickPair = () => {
      if (ids.length < 2) return null;
      for (let i = 0; i < 600; i++) {
        const a = ids[Math.floor(Math.random() * ids.length)];
        const b = ids[Math.floor(Math.random() * ids.length)];
        if (a === b) continue;
        const p = bfs(a, b);
        if (p && p.length >= 3 && p.length <= 5) return p;
      }
      for (let i = 0; i < 200; i++) {
        const a = ids[Math.floor(Math.random() * ids.length)];
        const b = ids[Math.floor(Math.random() * ids.length)];
        if (a === b) continue;
        const p = bfs(a, b);
        if (p && p.length >= 2) return p;
      }
      return null;
    };
    window.PathGame = { data, bfs, pickPair, pair: null, guess: null, score: 0, round: 1 };
    const first = pickPair();
    if (!first) {
      return `<h2><span class="icon">🧭</span> 连接游戏</h2><div class="card"><p>影响关系数据不足，无法开始游戏。先去「证据库」补充几条影响关系吧。</p></div>`;
    }
    window.PathGame.pair = first;
    return `
      <h2><span class="icon">🧭</span> 连接游戏 · 六度连接</h2>
      <p style="color:var(--text-secondary); margin-bottom:1rem;">两位学者隔着几步影响关系？猜猜看，然后用真实的学术传承路径验证你的直觉。</p>
      <div id="gameBody">${gameRoundHtml()}</div>
    `;
  },

  // ===== 成就 =====
  achievements: async () => {
    const st = AchievementManager.state;
    const cards = AchievementManager.defs.map(d => {
      const un = st.unlocked[d.id];
      const prog = d.progress ? Math.min(d.progress(st), d.goal) : (un ? 1 : 0);
      const pct = d.progress ? Math.round(prog / d.goal * 100) : (un ? 100 : 0);
      return `
        <div class="ach-card ${un ? 'unlocked' : ''}">
          <div class="ach-icon">${un ? d.icon : '🔒'}</div>
          <div class="ach-name">${escapeHtml(d.name)}</div>
          <div class="ach-desc">${escapeHtml(d.desc)}</div>
          ${d.progress
            ? `<div class="ach-progress"><div class="ach-bar" style="width:${pct}%"></div></div><div class="ach-count">${prog} / ${d.goal}</div>`
            : `<div class="ach-count">${un ? '✓ 已解锁' : '未解锁'}</div>`}
        </div>`;
    }).join('');
    return `
      <h2><span class="icon">🏅</span> 成就</h2>
      <div class="ach-stats">
        📌 已浏览 <b>${st.visitedScholars.length}</b> 位学者 ·
        🧠 竞答最佳 <b>${st.quizBest}</b> 分 ·
        🧭 猜中路径 <b>${st.pathWins}</b> 次 ·
        🎫 完成导览 <b>${st.toursDone.length}</b> 条 ·
        🏆 已解锁 <b>${Object.keys(st.unlocked).length}</b> / ${AchievementManager.defs.length}
      </div>
      <div class="ach-grid">${cards}</div>
      <div style="margin-top:1.5rem;">
        <button class="secondary" onclick="achReset()">🗑️ 清除进度</button>
      </div>
    `;
  }
};

// School name mapping (Chinese)
const SCHOOL_NAMES = {
  'SCHOOL_FRANKFURT': '法兰克福学派',
  'SCHOOL_COLUMBIA': '哥伦比亚学派',
  'SCHOOL_CHICAGO': '芝加哥学派',
  'SCHOOL_TORONTO': '多伦多学派',
  'SCHOOL_BIRMINGHAM': '伯明翰学派',
  'SCHOOL_CN_REFORM': '中国改良派',
  'SCHOOL_CN_REPUBLIC': '中国民国派',
  'SCHOOL_CN_CPC': '中国共产党报刊',
  'SCHOOL_CN_REVOLUTION': '中国革命派',
  'SCHOOL_CN_EARLY': '中国近代报刊',
  'SCHOOL_CN_LEFT': '中国左翼',
  'SCHOOL_CN_LIBERAL': '中国自由派',
  'SCHOOL_CN_ACADEMIC': '中国新闻学术',
  'SCHOOL_CN_CRITICAL': '中国批判派',
  'SCHOOL_HK_PRESS': '香港报业',
  'SCHOOL_JOURNALISM': '新闻业',
  'SCHOOL_PR': '公共关系',
  'SCHOOL_PENNY_PRESS': '便士报',
  'SCHOOL_BROADCAST': '广播电视',
  'SCHOOL_LIBERTARIAN': '自由主义',
  'SCHOOL_QUALITY_PRESS': '精英报刊',
  'SCHOOL_YELLOW_PRESS': '黄色新闻',
  'SCHOOL_MAGAZINE': '杂志业',
  'SCHOOL_YALE': '耶鲁学派',
  'SCHOOL_GROUP_DYNAMICS': '群体动力学',
  'SCHOOL_AGENDA': '议程设置',
  'SCHOOL_GERMAN': '德国传播学',
  'SCHOOL_CULTIVATION': '涵化理论',
  'SCHOOL_DIFFUSION': '创新扩散',
  'SCHOOL_CULTURAL_STUDIES': '文化研究',
  'SCHOOL_MEDIA_ECOLOGY': '媒介生态学',
  'SCHOOL_POLITICAL_ECO': '传播政治经济学',
  'SCHOOL_LINGUISTICS': '语言学',
  'SCHOOL_INFO_THEORY': '信息论',
  'SCHOOL_CYBERNETICS': '控制论',
  'SCHOOL_PRAGMATISM': '实用主义',
  'SCHOOL_USES_GRATIFICATIONS': '使用与满足',
  'SCHOOL_EUROPEAN': '欧洲传播学',
  'SCHOOL_SYSTEMS': '系统论',
  'SCHOOL_GERMAN_MEDIA': '德国媒介理论',
  'SCHOOL_NETWORK': '网络社会',
  'default': '学者'
};

// Helper function for school Chinese name
function getSchoolName(schoolId) {
  return SCHOOL_NAMES[schoolId] || schoolId.replace('SCHOOL_', '').replace(/_/g, ' ');
}

// Helper function for avatar colors
function getAvatarColor(schoolId) {
  const colors = {
    'SCHOOL_FRANKFURT': '#9b59b6',
    'SCHOOL_COLUMBIA': '#3498db',
    'SCHOOL_CHICAGO': '#e74c3c',
    'SCHOOL_TORONTO': '#1abc9c',
    'SCHOOL_BIRMINGHAM': '#f39c12',
    'SCHOOL_CN_REFORM': '#c0392b',
    'SCHOOL_CN_REPUBLIC': '#27ae60',
    'SCHOOL_CN_CPC': '#e74c3c',
    'SCHOOL_CN_REVOLUTION': '#8e44ad',
    'SCHOOL_JOURNALISM': '#2980b9',
    'SCHOOL_PR': '#16a085',
    'SCHOOL_PENNY_PRESS': '#d35400',
    'SCHOOL_BROADCAST': '#2c3e50',
    'SCHOOL_LIBERTARIAN': '#7f8c8d',
    'default': '#2c3e50'
  };
  return colors[schoolId] || colors['default'];
}

// ===== Curated storylines =====
const TOURS = [
  {
    id: 'free_speech',
    icon: '🗽',
    title: '言论自由之路',
    intro: '从弥尔顿在国会质询中捍卫出版自由，到梁启超以报刊「去塞求通」，再到哈贝马斯的公共领域——四百年间，人类为「说话的权利」走过漫长的路。',
    steps: [
      { scholar_id: 'SCH_MILTON', prop_id: 'PROP_MILTON_1644_A', caption: '1644 年，弥尔顿向英国国会发表《论出版自由》：真理与谬误应当在公开交锋中自我证明，事前检查制度是对真理的侮辱。' },
      { scholar_id: 'SCH_JEFFERSON', prop_id: 'PROP_JEFFERSON_1787_A', caption: '1787 年，杰斐逊留下名言：宁要没有政府的报纸，不要没有报纸的政府——新闻自由被抬到民主基石的高度。' },
      { scholar_id: 'SCH_LIANG_QICHAO', prop_id: 'PROP_LIANG_1898_A', caption: '大洋另一端，梁启超疾呼报刊应当「去塞求通」——报馆是国家的耳目与喉舌，中国的报人把办报当作救国的事业。' },
      { scholar_id: 'SCH_XU_BAOHUANG', caption: '1919 年，徐宝璜的《新闻学》出版，中国第一本新闻学著作。新闻自由开始从政治理想变为可以研究的学科命题。' },
      { scholar_id: 'SCH_HABERMAS', prop_id: 'PROP_HABERMAS_1962_A', caption: '1962 年，哈贝马斯重新描画言论自由的社会空间：公共领域——公民理性讨论、形成公共舆论的场所。' },
      { text: '🎯 导览结语', caption: '从弥尔顿的「观念市场」到哈贝马斯的「公共领域」，再到今天的算法平台——言论自由的故事仍在继续。下一站，去「连接游戏」里看看这些思想如何相互影响。' }
    ]
  },
  {
    id: 'media_ecology',
    icon: '🌱',
    title: '媒介环境学之旅',
    intro: '一支把「媒介本身」而非内容当作主角的学派：从伊尼斯的偏向，到麦克卢汉的讯息，再到波兹曼的警钟与莱文森的乐观。',
    steps: [
      { scholar_id: 'SCH_INNIS', caption: '伊尼斯《帝国与传播》(1950)：每种媒介都有时间或空间的「偏向」，媒介改变知识的形态，进而改变整个文明。' },
      { scholar_id: 'SCH_MCLUHAN', prop_id: 'PROP_MCLUHAN_1964_A', caption: '1964 年，麦克卢汉一锤定音：媒介即讯息——真正改变社会的不是媒介传递的内容，而是媒介本身。' },
      { scholar_id: 'SCH_POSTMAN', prop_id: 'PROP_POSTMAN_1985_A', caption: '1985 年，波兹曼敲响警钟：电视把一切公共话语变成娱乐，我们正在「娱乐至死」。' },
      { scholar_id: 'SCH_LEVINSON', prop_id: 'PROP_LEVINSON_1999_A', caption: '世纪之交，莱文森乐观回应：人是媒介的演化者，「新新媒介」延续并增强了人类的认知能力。' },
      { text: '🎯 导览结语', caption: '媒介环境学告诉我们：每引入一种新媒介，人类的居住环境就被重新「装修」一次。今天的人工智能，正是最新的那个房间。' }
    ]
  },
  {
    id: 'info_bubble',
    icon: '🫧',
    title: '从拟态环境到过滤气泡',
    intro: '一条贯穿百年的问题线：公众看到的究竟是真实世界，还是被制造出来的图景？',
    steps: [
      { scholar_id: 'SCH_LIPPMANN', prop_id: 'PROP_LIPPMANN_1922_A', caption: '1922 年，李普曼说：我们认识的世界是媒介构筑的「拟态环境」——头脑中的图像，而非世界本身。' },
      { scholar_id: 'SCH_TICHENOR', prop_id: 'PROP_TICHENOR_1970_A', caption: '1970 年，蒂奇诺发现信息流通的另一重扭曲：大众媒介越发达，不同群体之间的知识沟反而越大。' },
      { scholar_id: 'SCH_SUNSTEIN', prop_id: 'PROP_SUNSTEIN_2001_A', caption: '2001 年，桑斯坦警告网络时代的回音室：过度的自主选择让同质群体在回声中越来越极端。' },
      { scholar_id: 'SCH_PARISER', prop_id: 'PROP_PARISER_2011_A', caption: '2011 年，帕里泽命名「过滤气泡」：这一次，替我们筛选世界的不再是编辑，而是算法。' },
      { text: '🎯 导览结语', caption: '从编辑部到算法，把关人换了面孔，问题却依旧：谁来决定我们看见什么？去「证据库」可以看到这些论断的原始文献。' }
    ]
  }
];

// ===== Curated debates =====
const DEBATES = [
  {
    id: 'lippmann_dewey',
    icon: '⚖️',
    title: '专家还是公民？李普曼 vs 杜威',
    period: '1922–1927',
    context: '1922 年李普曼出版《舆论学》，断言普通公众没有能力了解复杂世界，公共事务应交由了解情况的专家；1927 年杜威以《公众及其问题》回应：公众并非「幽灵」，民主的生命在于共同体的参与与交流。这场论战奠定了此后百年关于民主与传播的基本张力。',
    left: { scholar_id: 'SCH_LIPPMANN', stance: '世界太复杂，公众只能依赖媒介构筑的「拟态环境」认识世界，重大决策应交给了解情况的专家。' },
    right: { scholar_id: 'SCH_DEWEY', stance: '民主不是治理的装饰品，公众可以在交流与共同体生活中学会治理自己。' }
  },
  {
    id: 'admin_critical',
    icon: '🧪',
    title: '管理研究还是批判研究？拉扎斯菲尔德 vs 阿多诺',
    period: '1940s',
    context: '1938 年，流亡美国的阿多诺受邀与哥伦比亚大学的拉扎斯菲尔德合作广播研究项目，两种学术取向正面相撞：实证的「管理研究」与哲学的「批判理论」。合作虽不欢而散，却成为传播研究史上被反复讲述的公案。',
    left: { scholar_id: 'SCH_LAZARSFELD', stance: '用实证方法测量传播效果，为改善媒介运营服务——先把问题搞清楚，再谈改造。' },
    right: { scholar_id: 'SCH_ADORNO', stance: '文化工业把艺术变成商品，大众传播是操纵意识的机器——必须整体批判，而非零敲碎打。' }
  },
  {
    id: 'mcluhan_williams',
    icon: '📺',
    title: '技术还是文化？麦克卢汉 vs 威廉斯',
    period: '1964–1974',
    context: '麦克卢汉断言「媒介即讯息」——技术形态决定社会的样貌。伯明翰学派的雷蒙·威廉斯随后反击：媒介技术本身就是社会与文化的产物，「技术决定论」是危险的简化。这场争论至今仍是理解每次媒介变革的思想坐标。',
    left: { scholar_id: 'SCH_MCLUHAN', stance: '媒介本身而非内容才是改造社会的力量——我们塑造工具，工具又塑造我们。' },
    right: { scholar_id: 'SCH_WILLIAMS', stance: '技术的社会用途由历史与文化决定：电视不是技术发明的必然结果，而是特定社会意向的产物。' }
  }
];

// ===== Quiz runtime =====
function buildQuizQuestions(data) {
  const qs = [];
  const scholars = (data.scholars || []).filter(s => s.name_zh);
  const scholarById = new Map(scholars.map(s => [s.scholar_id, s]));
  const props = (data.propositions || []).filter(p =>
    p.proposition_text_zh && p.proposition_text_zh.length >= 8 && p.scholar_id && scholarById.has(p.scholar_id));
  const books = (data.books || []).filter(b => b.title_zh && b.scholar_id && scholarById.has(b.scholar_id));

  shuffle(props).slice(0, 5).forEach(p => {
    const correct = scholarById.get(p.scholar_id);
    const wrong = shuffle(scholars.filter(s => s.scholar_id !== p.scholar_id)).slice(0, 3);
    if (wrong.length < 3) return;
    const text = p.proposition_text_zh.length > 60 ? p.proposition_text_zh.slice(0, 60) + '…' : p.proposition_text_zh;
    qs.push({
      prompt: `「${text}」\n这一命题出自哪位学者？`,
      options: shuffle([correct, ...wrong]).map(s => s.name_zh),
      answer: correct.name_zh
    });
  });
  shuffle(books).slice(0, 3).forEach(b => {
    const correct = scholarById.get(b.scholar_id);
    const wrong = shuffle(scholars.filter(s => s.scholar_id !== b.scholar_id)).slice(0, 3);
    if (wrong.length < 3) return;
    qs.push({
      prompt: `《${b.title_zh}》的作者是哪位学者？`,
      options: shuffle([correct, ...wrong]).map(s => s.name_zh),
      answer: correct.name_zh
    });
  });
  shuffle(props).slice(0, 2).forEach(p => {
    const y = parseInt(p.year, 10);
    if (!y) return;
    let offsets = shuffle([-20, -10, -5, -2, -1, 1, 2, 5, 10, 20]).map(d => y + d).filter(v => v > 0);
    const seen = new Set([y]);
    offsets = offsets.filter(v => { if (seen.has(v)) return false; seen.add(v); return true; }).slice(0, 3);
    while (offsets.length < 3) { const cand = y + 30 + offsets.length * 7; if (!seen.has(cand)) { offsets.push(cand); seen.add(cand); } }
    const text = p.proposition_text_zh.length > 40 ? p.proposition_text_zh.slice(0, 40) + '…' : p.proposition_text_zh;
    qs.push({
      prompt: `「${text}」\n这一命题提出于哪一年？`,
      options: shuffle([String(y), ...offsets.map(String)]),
      answer: String(y)
    });
  });
  return shuffle(qs).slice(0, 10);
}

function quizQuestionHtml() {
  const Q = window.Quiz;
  const q = Q.questions[Q.index];
  return `
    <div class="quiz-card">
      <div class="quiz-progress">第 ${Q.index + 1} / ${Q.questions.length} 题 · 当前得分 <b>${Q.score}</b></div>
      <p class="quiz-prompt">${escapeHtml(q.prompt).replace(/\n/g, '<br>')}</p>
      <div class="quiz-options">
        ${q.options.map(o => `<button class="quiz-opt" data-opt="${escapeHtml(o)}" onclick="quizAnswer(this)">${escapeHtml(o)}</button>`).join('')}
      </div>
      <div class="quiz-feedback" id="quizFeedback"></div>
    </div>`;
}

function quizResultHtml() {
  const Q = window.Quiz;
  const ratio = Q.score / Q.questions.length;
  const verdict = ratio === 1 ? '🏆 完美！你就是活的新闻传播学百科。'
    : ratio >= 0.7 ? '🎉 优秀！博物馆常客没跑了。'
    : ratio >= 0.4 ? '📚 不错，再逛逛学者名录还能涨分。'
    : '🐣 每位大学者都是从不熟悉开始的，去「主题导览」补补课吧！';
  return `
    <div class="quiz-card quiz-result">
      <div class="quiz-score">${Q.score}<span> / ${Q.questions.length}</span></div>
      <p style="margin:0.8rem 0;">${verdict}</p>
      <p style="color:var(--text-secondary); font-size:0.85rem; margin-bottom:1.2rem;">历史最佳：${AchievementManager.state.quizBest} 分</p>
      <button class="primary" onclick="quizRestart()">🔄 再来一轮</button>
      <button class="secondary" onclick="window.location.hash='dashboard'" style="margin-left:0.5rem;">回首页</button>
    </div>`;
}

window.quizAnswer = (btn) => {
  const Q = window.Quiz;
  if (!Q || Q.answered) return;
  Q.answered = true;
  const chosen = btn.getAttribute('data-opt');
  const q = Q.questions[Q.index];
  const correct = chosen === q.answer;
  if (correct) Q.score += 1;
  document.querySelectorAll('.quiz-opt').forEach(b => {
    const val = b.getAttribute('data-opt');
    b.disabled = true;
    if (val === q.answer) b.classList.add('correct');
    else if (val === chosen) b.classList.add('wrong');
  });
  const fb = document.getElementById('quizFeedback');
  if (fb) {
    fb.innerHTML = `
      <div class="quiz-verdict ${correct ? 'ok' : 'no'}">${correct ? '✅ 答对了！' : '❌ 正确答案：' + escapeHtml(q.answer)}</div>
      <button class="primary" style="margin-top:0.8rem;" onclick="quizNext()">${Q.index + 1 >= Q.questions.length ? '查看结果 →' : '下一题 →'}</button>`;
  }
};

window.quizNext = () => {
  const Q = window.Quiz;
  if (!Q) return;
  Q.index += 1;
  Q.answered = false;
  const body = document.getElementById('quizBody');
  if (!body) return;
  if (Q.index >= Q.questions.length) {
    AchievementManager.quizScore(Q.score);
    body.innerHTML = quizResultHtml();
  } else {
    body.innerHTML = quizQuestionHtml();
  }
};

window.quizRestart = async () => {
  const data = await API.browse();
  window.Quiz = { questions: buildQuizQuestions(data), index: 0, score: 0, answered: false };
  const body = document.getElementById('quizBody');
  if (body && window.Quiz.questions.length) body.innerHTML = quizQuestionHtml();
};

// ===== Path game runtime =====
function gameScholarChip(id, endpoint) {
  const G = window.PathGame;
  const s = (G.data.scholars || []).find(x => x.scholar_id === id);
  const name = s ? (s.name_zh || s.name_en || id) : id;
  return `
    <div class="path-chip ${endpoint ? 'endpoint' : ''}">
      <div class="path-avatar" style="background:${getAvatarColor(s ? s.school_id : undefined)};">${escapeHtml((name || '?')[0])}</div>
      <div class="path-name">${escapeHtml(name)}</div>
      ${endpoint ? `<div class="path-role">${endpoint === 'start' ? '起点' : '终点'}</div>` : ''}
    </div>`;
}

function gameRoundHtml() {
  const G = window.PathGame;
  const [a, b] = [G.pair[0], G.pair[G.pair.length - 1]];
  return `
    <div class="game-round">
      <div class="game-meta">第 ${G.round} 轮 · 累计得分 <b>${G.score}</b></div>
      <div class="game-endpoints">
        ${gameScholarChip(a, 'start')}
        <div class="game-question">之间隔着<br><b>几步</b><br>影响？</div>
        ${gameScholarChip(b, 'end')}
      </div>
      <div class="game-guess" id="gameGuess">
        ${[1, 2, 3, 4, 5].map(n => `<button class="btn-small" data-steps="${n}" onclick="gamePick(${n}, this)">${n} 步</button>`).join('')}
      </div>
      <div id="gameReveal"></div>
    </div>`;
}

window.gamePick = (n, btn) => {
  const G = window.PathGame;
  if (!G || G.revealed) return;
  G.guess = n;
  document.querySelectorAll('#gameGuess [data-steps]').forEach(b => b.classList.remove('selected'));
  if (btn) btn.classList.add('selected');
  let go = document.getElementById('gameGo');
  if (!go) {
    go = document.createElement('button');
    go.id = 'gameGo';
    go.className = 'primary';
    go.textContent = '🔮 揭晓答案';
    go.onclick = gameReveal;
    document.getElementById('gameGuess').appendChild(go);
  }
};

window.gameReveal = () => {
  const G = window.PathGame;
  if (!G || G.revealed || G.guess === null) return;
  G.revealed = true;
  const actual = G.pair.length - 1;
  const delta = Math.abs(G.guess - actual);
  const pts = delta === 0 ? 10 : (delta === 1 ? 5 : 0);
  G.score += pts;
  if (delta === 0) AchievementManager.pathWin();
  const verdict = delta === 0 ? `🎯 正中靶心！${G.pair[0] === G.pair[0] ? '' : ''}+10 分`
    : delta === 1 ? `🔥 只差一步！+5 分`
    : `实际隔 ${actual} 步（你猜了 ${G.guess} 步），+0 分`;
  const reveal = document.getElementById('gameReveal');
  if (reveal) {
    reveal.innerHTML = `
      <div class="game-verdict ${delta === 0 ? 'ok' : delta === 1 ? 'near' : 'no'}">${verdict}</div>
      <div class="path-stepper">
        ${G.pair.map((id, i) => gameScholarChip(id, i === 0 ? 'start' : i === G.pair.length - 1 ? 'end' : null) + (i < G.pair.length - 1 ? '<span class="path-arrow">➜</span>' : '')).join('')}
      </div>
      <button class="primary" style="margin-top:1rem;" onclick="gameNext()">🔀 下一对（得分 ${G.score}）</button>`;
  }
  const guessBox = document.getElementById('gameGuess');
  if (guessBox) guessBox.style.display = 'none';
};

window.gameNext = () => {
  const G = window.PathGame;
  if (!G) return;
  const pair = G.pickPair();
  if (!pair) { showToast('影响关系数据不足，游戏结束'); return; }
  G.pair = pair;
  G.guess = null;
  G.revealed = false;
  G.round += 1;
  const body = document.getElementById('gameBody');
  if (body) body.innerHTML = gameRoundHtml();
};

// ===== Tour runtime =====
window.tourGo = async (delta) => {
  const state = window.__tour;
  if (!state) return;
  const t = TOURS.find(x => x.id === state.id);
  if (!t) return;
  const n = t.steps.length;
  state.i = Math.min(Math.max(state.i + delta, 0), n - 1);
  const i = state.i;
  const step = t.steps[i];

  let inner = '';
  if (step.text) {
    inner = `<div class="tour-caption standalone">${escapeHtml(step.caption)}</div>`;
  } else {
    const data = await API.browse();
    const s = (data.scholars || []).find(x => x.scholar_id === step.scholar_id);
    const quote = (data.quotes || []).find(q => q.scholar_id === step.scholar_id);
    const props = (data.propositions || []).filter(p => p.scholar_id === step.scholar_id);
    const prop = step.prop_id ? props.find(p => p.proposition_id === step.prop_id) : (props[0] || null);
    inner = `
      <div class="tour-caption">${escapeHtml(step.caption)}</div>
      ${s ? `
      <div class="tour-scholar">
        <div class="scholar-avatar" style="background:${getAvatarColor(s.school_id)}; width:56px; height:56px; font-size:1.4rem; margin:0;">${escapeHtml(((s.name_zh || s.name_en || '?'))[0])}</div>
        <div>
          <div><a href="#scholar/${escapeHtml(s.scholar_id)}" style="color:var(--accent-color); font-weight:600; text-decoration:none; font-size:1.05rem;">${escapeHtml(s.name_zh || s.name_en || s.scholar_id)}</a> <small style="color:var(--text-secondary);">${escapeHtml(s.name_en || '')}</small></div>
          <div class="badge scholar" style="margin-top:0.3rem;">${escapeHtml(s.school_id ? getSchoolName(s.school_id) : '学者')}</div>
        </div>
      </div>` : ''}
      ${prop ? `<div class="tour-prop"><span class="badge event">${escapeHtml(prop.year)}</span><p>${escapeHtml(prop.proposition_text_zh)}</p></div>` : ''}
      ${quote ? `<div class="quote-card small"><div class="quote-text">${escapeHtml(quote.quote_zh)}</div><div class="quote-ctx">—— ${escapeHtml(quote.context_zh || '')}</div></div>` : ''}
      ${s ? `<a class="tour-open" href="#scholar/${escapeHtml(s.scholar_id)}">打开完整学者主页 →</a>` : ''}`;
  }

  const stepEl = document.getElementById('tourStep');
  if (stepEl) stepEl.innerHTML = inner;
  t.steps.forEach((_, k) => {
    const d = document.getElementById('td' + k);
    if (d) d.className = 'tour-dot' + (k <= i ? ' done' : '') + (k === i ? ' active' : '');
  });
  const pos = document.getElementById('tourPos');
  if (pos) pos.textContent = `${i + 1} / ${n}`;
  const prev = document.getElementById('tourPrev');
  if (prev) prev.disabled = i === 0;
  const next = document.getElementById('tourNext');
  if (next) next.textContent = i === n - 1 ? '完成导览 ✓' : '下一站 →';
  if (i === n - 1 && delta > 0) AchievementManager.tourCompleted(t.id);
};

// ===== View-mode switchers & misc =====
window.setTlMode = (m) => { localStorage.setItem('tl_mode', m); render('timeline'); };
window.setConceptTab = (m) => { localStorage.setItem('concept_tab', m); render('concepts'); };

window.randomScholar = async () => {
  try {
    const data = await API.browse();
    const list = data.scholars || [];
    if (!list.length) return;
    const s = list[Math.floor(Math.random() * list.length)];
    showToast(`🎲 随机遇见：${s.name_zh || s.name_en}`);
    window.location.hash = `scholar/${s.scholar_id}`;
  } catch (e) { /* 静默失败即可 */ }
};

window.achReset = () => {
  if (!confirm('确定清除所有成就进度吗？此操作不可恢复。')) return;
  localStorage.removeItem(AchievementManager.KEY);
  AchievementManager.load();
  render('achievements');
};

// Router
let renderSeq = 0;
const render = async (rawRoute) => {
  const seq = ++renderSeq;
  // Hash segments stay percent-encoded (e.g. Chinese search terms), decode before use
  const parts = rawRoute.split('/');
  const route = safeDecode(parts[0] || '');
  const param = parts.length > 1 ? safeDecode(parts.slice(1).join('/')) : undefined;

  const app = document.getElementById('view');
  app.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  document.querySelector('.main').scrollTop = 0;

  document.querySelectorAll('.navBtn').forEach(b => b.classList.remove('active'));
  const btn = document.querySelector(`[data-route="${CSS.escape(route)}"]`);
  if(btn) btn.classList.add('active');

  try {
    if (VIEWS[route]) {
      const content = await VIEWS[route](param);
      if (seq !== renderSeq) return; // a newer navigation already took over
      app.innerHTML = content;
      document.querySelector('.main').scrollTop = 0;
    } else {
      app.innerHTML = `<div class="card" style="border-color:#e74c3c;"><h3>页面未找到</h3><p>Route: ${escapeHtml(route)}</p></div>`;
    }
  } catch (e) {
    console.error(e);
    if (seq !== renderSeq) return;
    app.innerHTML = `<div class="card" style="border-color:#e74c3c;"><h3>加载错误</h3><p>${escapeHtml(e.message)}</p></div>`;
  }
};

// Export functions
window.exportJSON = async () => {
  const data = await API.browse();
  const jsonStr = JSON.stringify(data, null, 2);
  const blob = new Blob([jsonStr], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'news-journalism-kg-data.json';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};

window.exportRDF = async () => {
  if (IS_STATIC) {
    alert('静态模式下无法导出 RDF，请使用 JSON 导出。');
    return;
  }
  try {
    const res = await fetch('/api/export/rdf', { method: 'POST' });
    const result = await res.json();
    if (result.ok) {
      window.open(result.download, '_blank');
    } else if (result.summary && result.summary.errors > 0) {
      alert(`导出失败：数据存在 ${result.summary.errors} 个校验错误，请先在「校验工具」中修复。`);
    } else {
      alert('导出失败：' + (result.error || '未知错误'));
    }
  } catch (e) {
    alert('导出失败：' + e.message);
  }
};

// CSV handlers
window.loadCsv = async () => {
  const select = document.getElementById('csvSelect');
  if (!select || !select.value) {
    alert('没有可选择的数据文件');
    return;
  }
  const name = select.value;
  try {
    const res = await API.get_csv(name);
    if (res.error || res.content === undefined) {
      alert('加载失败：' + (res.error || '服务器未返回内容'));
      return;
    }
    document.getElementById('csvEditor').value = res.content;
  } catch (e) {
    alert('加载失败：' + e.message);
  }
};

window.saveCsv = async () => {
  const select = document.getElementById('csvSelect');
  if (!select || !select.value) {
    alert('没有可选择的数据文件');
    return;
  }
  const name = select.value;
  const content = document.getElementById('csvEditor').value;
  if (!confirm(`确定要保存并覆盖 ${name} 吗？\n建议先在「校验工具」中检查数据。`)) return;
  try {
    const res = await API.save_csv(name, content);
    if (res.ok) {
      alert('保存成功！');
    } else {
      alert('保存失败：' + (res.error || '服务器拒绝写入'));
    }
  } catch (e) {
    alert('保存失败：' + e.message);
  }
};

// Physics control functions for dynamic network
let physicsEnabled = true;

window.togglePhysics = () => {
  const network = window.graphNetwork || window.influenceNetwork;
  if (!network) return;

  physicsEnabled = !physicsEnabled;
  network.setOptions({ physics: { enabled: physicsEnabled } });

  const btn = document.getElementById('physicsBtn');
  if (btn) {
    btn.innerHTML = physicsEnabled ? '⏸️ 暂停动画' : '▶️ 启动动画';
  }
};

window.shakeNetwork = () => {
  const network = window.graphNetwork || window.influenceNetwork;
  if (!network) return;

  network.setOptions({ physics: { enabled: true } });
  physicsEnabled = true;

  const btn = document.getElementById('physicsBtn');
  if (btn) {
    btn.innerHTML = '⏸️ 暂停动画';
  }

  network.stabilize(50);
};

// Map zoom control
let mapZoomLevel = 100;
const MAP_ZOOM_MIN = 50;
const MAP_ZOOM_MAX = 200;
const MAP_ZOOM_STEP = 25;

window.mapZoom = (action) => {
  const container = document.getElementById('mapContainer');
  const inner = container ? container.querySelector('.map-inner') : null;
  const zoomLabel = document.getElementById('zoomLevel');

  if (!container || !inner) return;

  // Calculate new zoom level
  if (action === 'in') {
    mapZoomLevel = Math.min(mapZoomLevel + MAP_ZOOM_STEP, MAP_ZOOM_MAX);
  } else if (action === 'out') {
    mapZoomLevel = Math.min(Math.max(mapZoomLevel - MAP_ZOOM_STEP, MAP_ZOOM_MIN), MAP_ZOOM_MAX);
  } else if (action === 'reset') {
    mapZoomLevel = 100;
  }

  // Apply zoom transform
  const scale = mapZoomLevel / 100;
  inner.style.transform = `scale(${scale})`;
  inner.style.transformOrigin = 'top left';

  // Update zoom label
  if (zoomLabel) {
    zoomLabel.textContent = `缩放: ${mapZoomLevel}%`;
  }
};

// Event listeners
document.getElementById('nav').addEventListener('click', (e) => {
  const btn = e.target.closest('.navBtn');
  if (btn && btn.dataset.route) {
    window.location.hash = btn.dataset.route;
  }
});

document.getElementById('themeToggle').addEventListener('click', () => {
  ThemeManager.toggle();
  if (document.documentElement.getAttribute('data-theme') === 'dark') AchievementManager.darkMode();
  // Re-render so canvas-rendered graphs pick up the new font colors
  const hash = window.location.hash.slice(1);
  render(hash || 'dashboard');
});

// Trigger search from the input or the magnifier button
function submitGlobalSearch() {
  const input = document.getElementById('globalSearch');
  const query = input.value.trim();
  if (query) {
    window.location.hash = `search/${encodeURIComponent(query)}`;
  }
}

document.getElementById('globalSearch').addEventListener('keypress', (e) => {
  if (e.key === 'Enter') submitGlobalSearch();
});

const searchBtn = document.getElementById('searchBtn');
if (searchBtn) searchBtn.addEventListener('click', submitGlobalSearch);

window.addEventListener('hashchange', () => {
  const hash = window.location.hash.slice(1);
  render(hash || 'dashboard');
});

// Boot
API.init().then(() => {
  AchievementManager.load();
  AchievementManager.unlock('first_visit');
  const hash = window.location.hash.slice(1);
  render(hash || 'dashboard');
});
