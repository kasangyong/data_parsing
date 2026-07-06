/* 엔티티 지식 그래프 뷰 — 온톨로지 기반 (엔티티 노드 + 관계 엣지).
   graph.js 와 물리 엔진 구조는 같으나, 노드가 문서가 아니라 엔티티이고
   엣지가 방향성 있는 관계(relation)라는 점이 다르다. 의존성 없이 캔버스로 그린다. */

// 엔티티 타입별 색상 (범례와 노드에 공통 사용)
const ENTITY_COLORS = {
  Person: "#e8590c",
  Organization: "#1971c2",
  Location: "#2f9e44",
  Product: "#9c36b5",
  Concept: "#1098ad",
  Event: "#e03131",
  Date: "#868e96",
  Metric: "#f08c00",
  Document: "#343a40",
};
const ENTITY_COLOR_DEFAULT = "#5c5f66";

const ENTITY_TYPE_LABELS = {
  Person: "인물",
  Organization: "조직",
  Location: "장소",
  Product: "제품",
  Concept: "개념",
  Event: "사건",
  Date: "날짜",
  Metric: "수치",
  Document: "문서",
};

const RELATION_LABELS = {
  works_for: "소속",
  located_in: "위치",
  produces: "생산",
  part_of: "하위",
  uses: "사용",
  related_to: "연관",
  mentions: "언급",
  defines: "정의",
  measured_as: "측정",
  occurred_on: "발생",
  authored_by: "저자",
  references: "참조",
  created_on: "작성일",
};

function entityColor(type) {
  return ENTITY_COLORS[type] || ENTITY_COLOR_DEFAULT;
}

class EntityGraph {
  constructor(canvas, panel) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.panel = panel;

    this.nodes = [];
    this.edges = [];
    this.nodeById = new Map();

    this.hoverNode = null;
    this.hoverEdge = null;
    this.selectedNode = null;
    this.dragNode = null;
    this.running = false;

    // 물리 파라미터 (엔티티는 노드 수가 많을 수 있어 반발력을 약간 낮춤)
    // 연결선 없는 고립 노드가 많을 때도(관계보다 노드가 훨씬 많음) 빠르게
    // 자리를 잡도록 마찰(damping)을 키우고 프레임당 이동량을 더 낮게 제한한다.
    this.repulsion = 16000;
    this.springLength = 130;
    this.springK = 0.04;
    this.damping = 0.72;
    this.centerPull = 0.014;
    this.maxStep = 8;

    this._bindEvents();
  }

  setData({ nodes, edges }) {
    const W = this.canvas.clientWidth || 900;
    const H = this.canvas.clientHeight || 600;
    this.nodes = nodes.map((n, i) => {
      const angle = (2 * Math.PI * i) / Math.max(nodes.length, 1);
      const r = Math.min(W, H) * 0.34;
      return {
        ...n,
        x: W / 2 + r * Math.cos(angle) + (Math.random() - 0.5) * 60,
        y: H / 2 + r * Math.sin(angle) + (Math.random() - 0.5) * 60,
        vx: 0, vy: 0,
        radius: Math.min(9 + Math.sqrt(n.mention_count || 1) * 2.2, 26),
      };
    });
    this.nodeById = new Map(this.nodes.map((n) => [n.id, n]));
    this.edges = edges.filter(
      (e) => this.nodeById.has(e.source) && this.nodeById.has(e.target)
    );
    this.selectedNode = null;
    this.hoverNode = null;
    this.hoverEdge = null;
    this._renderPanel();
    this.start();
  }

  start() {
    if (this.running) return;
    this.running = true;
    const loop = () => {
      if (!this.running) return;
      this._resize();
      this._physics();
      this._draw();
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  stop() { this.running = false; }

  _resize() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (this.canvas.width !== w * dpr || this.canvas.height !== h * dpr) {
      this.canvas.width = w * dpr;
      this.canvas.height = h * dpr;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
  }

  _physics() {
    const W = this.canvas.clientWidth, H = this.canvas.clientHeight;
    const nodes = this.nodes;
    // 노드가 많을수록(엔티티 그래프는 연결 안 된 노드가 많을 수 있음) 반발력을
    // 낮춰서 서로 계속 튕겨내며 흔들리는 현상을 줄인다.
    const repulsion = this.repulsion / Math.max(1, Math.sqrt(nodes.length / 40));

    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
        const f = repulsion / d2;
        const d = Math.sqrt(d2);
        const fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx += fx; a.vy += fy;
        b.vx -= fx; b.vy -= fy;
      }
    }

    for (const e of this.edges) {
      const a = this.nodeById.get(e.source), b = this.nodeById.get(e.target);
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const target = this.springLength * (1.3 - (e.confidence || 0.5) * 0.6);
      const f = this.springK * (d - target);
      const fx = (dx / d) * f, fy = (dy / d) * f;
      a.vx += fx; a.vy += fy;
      b.vx -= fx; b.vy -= fy;
    }

    for (const n of nodes) {
      n.vx += (W / 2 - n.x) * this.centerPull;
      n.vy += (H / 2 - n.y) * this.centerPull;
      if (n === this.dragNode) { n.vx = 0; n.vy = 0; continue; }
      n.vx *= this.damping;
      n.vy *= this.damping;
      n.x += Math.max(-this.maxStep, Math.min(this.maxStep, n.vx));
      n.y += Math.max(-this.maxStep, Math.min(this.maxStep, n.vy));
      n.x = Math.max(n.radius + 4, Math.min(W - n.radius - 4, n.x));
      n.y = Math.max(n.radius + 4, Math.min(H - n.radius - 4, n.y));
    }
  }

  _draw() {
    const ctx = this.ctx;
    const W = this.canvas.clientWidth, H = this.canvas.clientHeight;
    ctx.clearRect(0, 0, W, H);

    if (!this.nodes.length) {
      ctx.fillStyle = "#7a7a7a";
      ctx.font = "15px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("엔티티 그래프가 비어 있습니다. [그래프 구축] 을 눌러주세요.",
        W / 2, H / 2);
      return;
    }

    const related = this._relatedSet();

    // ----- 엣지 (방향성: source → target 화살표) -----
    for (const e of this.edges) {
      const a = this.nodeById.get(e.source), b = this.nodeById.get(e.target);
      const active = e === this.hoverEdge ||
        (this.selectedNode &&
          (e.source === this.selectedNode.id || e.target === this.selectedNode.id));
      const dimmed = (this.selectedNode || this.hoverNode) && !active &&
        !this._edgeTouches(e, this.hoverNode);

      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.hypot(dx, dy) || 1;
      const ux = dx / d, uy = dy / d;
      const sx = a.x + ux * a.radius, sy = a.y + uy * a.radius;
      const ex = b.x - ux * b.radius, ey = b.y - uy * b.radius;

      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(ex, ey);
      ctx.lineWidth = 1 + (e.confidence || 0.4) * 3;
      ctx.strokeStyle = active
        ? "rgba(0, 102, 204, 0.85)"
        : dimmed ? "rgba(0, 0, 0, 0.04)" : "rgba(0, 0, 0, 0.14)";
      ctx.stroke();

      // 화살촉
      if (!dimmed) {
        const ah = 7, aw = 4;
        ctx.beginPath();
        ctx.moveTo(ex, ey);
        ctx.lineTo(ex - ux * ah - uy * aw, ey - uy * ah + ux * aw);
        ctx.lineTo(ex - ux * ah + uy * aw, ey - uy * ah - ux * aw);
        ctx.closePath();
        ctx.fillStyle = active ? "rgba(0,102,204,0.85)" : "rgba(0,0,0,0.2)";
        ctx.fill();
      }

      // 활성 엣지에 관계명 라벨
      if (active) {
        const mx = (sx + ex) / 2, my = (sy + ey) / 2;
        const label = RELATION_LABELS[e.relation] || e.relation;
        ctx.font = "600 11px system-ui, sans-serif";
        const tw = ctx.measureText(label).width;
        ctx.fillStyle = "rgba(255,255,255,0.94)";
        this._roundRect(mx - tw / 2 - 6, my - 10, tw + 12, 20, 10);
        ctx.fill();
        ctx.strokeStyle = "rgba(0,102,204,0.4)";
        ctx.lineWidth = 1;
        this._roundRect(mx - tw / 2 - 6, my - 10, tw + 12, 20, 10);
        ctx.stroke();
        ctx.fillStyle = "#0066cc";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(label, mx, my);
        ctx.textBaseline = "alphabetic";
      }
    }

    // ----- 노드 -----
    for (const n of this.nodes) {
      const isSel = n === this.selectedNode;
      const isHover = n === this.hoverNode;
      const isRelated = related.has(n.id);
      const dimmed = (this.selectedNode || this.hoverNode) &&
        !isSel && !isHover && !isRelated;
      const color = entityColor(n.entity_type);

      ctx.globalAlpha = dimmed ? 0.22 : 1;
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      if (isSel || isHover) {
        ctx.lineWidth = 3;
        ctx.strokeStyle = "rgba(0,102,204,0.55)";
        ctx.stroke();
      }
      ctx.globalAlpha = 1;

      // 라벨
      const label = this._shortLabel(n.label);
      ctx.font = `${isSel || isHover ? "600 " : ""}11px system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.fillStyle = dimmed ? "rgba(29,29,31,0.25)" : "#1d1d1f";
      ctx.fillText(label, n.x, n.y + n.radius + 13);
    }
  }

  _roundRect(x, y, w, h, r) {
    const ctx = this.ctx;
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  _shortLabel(name) {
    const base = String(name).replace(/\.pdf$/i, "");
    return base.length > 16 ? base.slice(0, 15) + "…" : base;
  }

  _relatedSet() {
    const focus = this.selectedNode || this.hoverNode;
    const set = new Set();
    if (!focus) return set;
    for (const e of this.edges) {
      if (e.source === focus.id) set.add(e.target);
      if (e.target === focus.id) set.add(e.source);
    }
    return set;
  }

  _edgeTouches(e, node) {
    return node && (e.source === node.id || e.target === node.id);
  }

  // ---------- 상세 패널 ----------

  _renderPanel() {
    if (!this.panel) return;
    const esc = (s) => {
      const d = document.createElement("div");
      d.textContent = s ?? "";
      return d.innerHTML;
    };

    if (!this.selectedNode) {
      this.panel.innerHTML = `
        <div class="graph-panel-empty">
          <p><strong>노드</strong> = 엔티티 · <strong>색</strong> = 종류 · <strong>화살표</strong> = 관계 방향</p>
          <p>원이 <strong>클수록</strong> 여러 곳에서 언급된 엔티티입니다.</p>
          <p>노드를 클릭하면 어느 문서·페이지에서 나왔는지 보여드립니다.</p>
        </div>`;
      return;
    }

    const n = this.selectedNode;
    const color = entityColor(n.entity_type);
    const typeLabel = ENTITY_TYPE_LABELS[n.entity_type] || n.entity_type;

    const connections = this.edges
      .filter((e) => e.source === n.id || e.target === n.id)
      .map((e) => {
        const outgoing = e.source === n.id;
        const other = this.nodeById.get(outgoing ? e.target : e.source);
        return { other, outgoing, relation: e.relation, confidence: e.confidence };
      })
      .filter((c) => c.other)
      .sort((a, b) => (b.confidence || 0) - (a.confidence || 0));

    const connHtml = connections.length
      ? connections.map((c) => {
          const rel = RELATION_LABELS[c.relation] || c.relation;
          const arrow = c.outgoing ? "→" : "←";
          return `<div class="graph-conn">
            <div class="graph-conn-head">
              <span class="graph-conn-name">${arrow} ${esc(this._shortLabel(c.other.label))}</span>
              <span class="score-badge">${Math.round((c.confidence || 0) * 100)}%</span>
            </div>
            <div class="graph-conn-kw"><span class="chip">${esc(rel)}</span>
              <span class="doc-meta">${esc(ENTITY_TYPE_LABELS[c.other.entity_type] || c.other.entity_type)}</span></div>
          </div>`;
        }).join("")
      : `<p class="graph-panel-muted">연결된 다른 엔티티가 없습니다.</p>`;

    const aliasHtml = (n.aliases && n.aliases.length)
      ? `<div class="graph-conn-kw" style="margin:8px 0 12px;">별칭: ${
          n.aliases.slice(0, 6).map((a) => `<span class="chip">${esc(a)}</span>`).join("")}</div>`
      : "";

    this.panel.innerHTML = `
      <h3 class="graph-panel-title">
        <span class="entity-dot" style="background:${color}"></span>${esc(n.label)}
      </h3>
      <p class="graph-panel-muted">
        ${esc(typeLabel)} · 타입 신뢰도 ${Math.round((n.type_confidence || 0) * 100)}%
        · 언급 ${n.mention_count} · 문서 ${n.document_count}
      </p>
      ${aliasHtml}
      <h4 class="graph-panel-sub">관계 (${connections.length})</h4>
      ${connHtml}
      <h4 class="graph-panel-sub">출처</h4>
      <div id="entity-mentions" class="graph-panel-muted">불러오는 중…</div>
    `;
    this._loadMentions(n.id);
  }

  async _loadMentions(entityId) {
    const el = this.panel.querySelector("#entity-mentions");
    if (!el) return;
    const esc = (s) => {
      const d = document.createElement("div");
      d.textContent = s ?? "";
      return d.innerHTML;
    };
    try {
      const res = await fetch(`/api/kg/entity/${entityId}`);
      if (!res.ok) throw new Error();
      const data = await res.json();
      if (!data.mentions.length) {
        el.textContent = "출처 정보가 없습니다.";
        return;
      }
      // 문서별로 묶어서 표시
      const byDoc = new Map();
      for (const m of data.mentions) {
        if (!byDoc.has(m.filename)) byDoc.set(m.filename, []);
        byDoc.get(m.filename).push(m);
      }
      el.classList.remove("graph-panel-muted");
      el.innerHTML = [...byDoc.entries()].map(([fname, ms]) => {
        const pages = [...new Set(ms.map((m) => m.page_number))]
          .sort((a, b) => a - b).slice(0, 8).map((p) => `p.${p}`).join(", ");
        const span = ms.find((m) => m.span_text)?.span_text || "";
        return `<div class="graph-conn">
          <div class="graph-conn-head">
            <span class="graph-conn-name">${esc(fname)}</span>
          </div>
          <div class="doc-meta">${esc(pages)}${span ? ` · “${esc(span.slice(0, 60))}”` : ""}</div>
        </div>`;
      }).join("");
    } catch {
      el.textContent = "출처를 불러올 수 없습니다.";
    }
  }

  // ---------- 마우스 인터랙션 ----------

  _pos(ev) {
    const r = this.canvas.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }

  _nodeAt(x, y) {
    for (let i = this.nodes.length - 1; i >= 0; i--) {
      const n = this.nodes[i];
      const dx = x - n.x, dy = y - n.y;
      if (dx * dx + dy * dy <= (n.radius + 4) ** 2) return n;
    }
    return null;
  }

  _edgeAt(x, y) {
    let best = null, bestDist = 8;
    for (const e of this.edges) {
      const a = this.nodeById.get(e.source), b = this.nodeById.get(e.target);
      const d = this._distToSegment(x, y, a.x, a.y, b.x, b.y);
      if (d < bestDist) { best = e; bestDist = d; }
    }
    return best;
  }

  _distToSegment(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1, dy = y2 - y1;
    const len2 = dx * dx + dy * dy || 1;
    let t = ((px - x1) * dx + (py - y1) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    const cx = x1 + t * dx, cy = y1 + t * dy;
    return Math.hypot(px - cx, py - cy);
  }

  _bindEvents() {
    this.canvas.addEventListener("mousemove", (ev) => {
      const { x, y } = this._pos(ev);
      if (this.dragNode) { this.dragNode.x = x; this.dragNode.y = y; return; }
      this.hoverNode = this._nodeAt(x, y);
      this.hoverEdge = this.hoverNode ? null : this._edgeAt(x, y);
      this.canvas.style.cursor = this.hoverNode ? "pointer"
        : this.hoverEdge ? "crosshair" : "default";
    });

    this.canvas.addEventListener("mousedown", (ev) => {
      const { x, y } = this._pos(ev);
      const n = this._nodeAt(x, y);
      if (n) this.dragNode = n;
    });

    window.addEventListener("mouseup", () => { this.dragNode = null; });

    this.canvas.addEventListener("click", (ev) => {
      const { x, y } = this._pos(ev);
      const n = this._nodeAt(x, y);
      this.selectedNode = n === this.selectedNode ? null : n;
      this._renderPanel();
    });

    this.canvas.addEventListener("mouseleave", () => {
      this.hoverNode = null;
      this.hoverEdge = null;
    });
  }
}

// ===== 엔티티 그래프 뷰 컨트롤러 =====

class EntityGraphView {
  constructor() {
    this.graph = null;
    this.loaded = false;
  }

  _renderLegend(stats) {
    const el = document.querySelector("#entity-legend");
    if (!el) return;
    const byType = (stats && stats.entities_by_type) || {};
    const items = Object.keys(ENTITY_COLORS)
      .filter((t) => byType[t])
      .map((t) => `<span class="entity-legend-item">
        <span class="entity-dot" style="background:${ENTITY_COLORS[t]}"></span>
        ${ENTITY_TYPE_LABELS[t]} <span class="doc-meta">${byType[t]}</span>
      </span>`).join("");
    el.innerHTML = items || `<span class="doc-meta">엔티티 없음 — [그래프 구축]을 눌러주세요.</span>`;
  }

  async load(force = false) {
    const canvas = document.querySelector("#entity-canvas");
    const panel = document.querySelector("#entity-panel");
    const statusEl = document.querySelector("#entity-status");
    if (!canvas) return;

    if (!this.graph) this.graph = new EntityGraph(canvas, panel);
    if (this.loaded && !force) { this.graph.start(); return; }

    const conf = document.querySelector("#entity-confidence")?.value ?? 0.3;
    statusEl.textContent = "엔티티 그래프 불러오는 중…";
    try {
      const res = await fetch(`/api/kg/graph?min_confidence=${conf}`);
      if (!res.ok) throw new Error("서버 오류");
      const data = await res.json();
      this.graph.setData(data);
      this._renderLegend(data.stats);
      this.loaded = true;
      if (!data.nodes.length) {
        statusEl.textContent = "엔티티가 없습니다. [그래프 구축]을 눌러주세요.";
      } else {
        statusEl.textContent =
          `엔티티 ${data.nodes.length}개 · 관계 ${data.edges.length}개`;
      }
    } catch {
      statusEl.textContent = "그래프를 불러올 수 없습니다.";
    }
  }

  async build() {
    const statusEl = document.querySelector("#entity-status");
    statusEl.textContent = "그래프 구축 중… (문서 수에 따라 시간이 걸립니다)";
    try {
      const res = await fetch("/api/kg/build", { method: "POST" });
      if (res.status === 503) {
        statusEl.textContent = "임베딩 모델이 필요합니다. (pdfsearch models)";
        return;
      }
      if (!res.ok) throw new Error();
      const r = await res.json();
      let msg = `구축 완료 — 엔티티 ${r.entities} · 관계 ${r.relations}`;
      if (r.violations) msg += ` · 온톨로지 위반 ${r.violations}건 거부`;
      if (!r.used_gliner) msg += " · (GLiNER 미설치: 구조/정규식만 사용)";
      statusEl.textContent = msg;
      await this.load(true);
    } catch {
      statusEl.textContent = "그래프 구축에 실패했습니다.";
    }
  }
}

const entityGraphView = new EntityGraphView();
