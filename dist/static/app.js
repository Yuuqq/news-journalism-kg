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
        influences: GLOBAL_DB.influences || []
      };
    } else {
      const res = await fetch('/api/browse');
      if (!res.ok) throw new Error(`服务器返回 ${res.status}，请稍后重试`);
      data = await res.json();
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
      edges.push({
        from: inf.object_id,
        to: inf.subject_id,
        arrows: { to: { enabled: true, scaleFactor: 0.6 } },
        title: escapeHtml(inf.note_zh),
        color: { color: '#bdc3c7', highlight: '#e74c3c' },
        width: 1.5
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

        network.on("click", params => {
          if (params.nodes.length > 0) {
            const nodeId = params.nodes[0];
            if (nodeId.startsWith('SCH_')) {
              window.location.hash = `scholar/${nodeId}`;
            }
          }
        });
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
      <div class="physics-controls" style="margin-bottom:1rem; display:flex; gap:0.5rem; flex-wrap:wrap;">
        <button class="primary" onclick="togglePhysics()" id="physicsBtn">⏸️ 暂停动画</button>
        <button class="primary" onclick="shakeNetwork()">🔀 重新排列</button>
        <button class="primary" onclick="window.influenceNetwork && window.influenceNetwork.fit()">📐 自适应</button>
        <span style="color:var(--text-secondary); font-size:0.85rem; margin-left:auto; line-height:2.2;">拖动节点 · 滚轮缩放 · 点击查看详情</span>
      </div>
      <div id="influence-viz" style="height:700px;"></div>

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

    let itemsHtml = '';
    props.forEach((p, idx) => {
      const scholar = data.scholars.find(s => s.scholar_id === p.scholar_id);
      const title = scholar ? scholar.name_zh : '历史事件';
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

    return `
      <h2><span class="icon">⏳</span> 新闻传播思想编年史</h2>
      <div style="margin-bottom:1rem; color:var(--text-secondary);">
        共 ${props.length} 个历史节点 ·
        <span class="badge event">橙色 = 历史事件</span>
        <span class="badge scholar">蓝色 = 学者理论</span>
      </div>
      <div class="timeline-container">
        <div class="timeline-line"></div>
        ${itemsHtml}
      </div>
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

    let html = `
      <h2><span class="icon">💡</span> 核心概念</h2>
      <p style="color:var(--text-secondary); margin-bottom:1.5rem;">共收录 ${concepts.length} 个传播学核心概念</p>
      <div class="card-grid">`;

    concepts.forEach(c => {
      const related = propsByConcept[c.concept_id] || [];
      html += `
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

    html += '</div>';
    return html;
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
        <span class="hint">拖动节点调整布局 · 滚轮缩放 · 点击查看详情</span>
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

        network.on("click", params => {
          if (params.nodes.length > 0) {
            const nodeId = params.nodes[0];
            if (nodeId.startsWith('SCH_')) {
              window.location.hash = `scholar/${nodeId}`;
            }
          }
        });

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
  const hash = window.location.hash.slice(1);
  render(hash || 'dashboard');
});
