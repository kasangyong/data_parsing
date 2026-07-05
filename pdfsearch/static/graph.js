/* 지식 그래프 뷰 — 문서 간 연결을 포스 레이아웃으로 시각화 (클래스 기반, 의존성 없음) */

class KnowledgeGraph {
  /**
   * @param {HTMLCanvasElement} canvas 그래프를 그릴 캔버스
   * @param {HTMLElement} panel 노드/엣지 상세를 표시할 사이드 패널
   */
  constructor(canvas, panel) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.panel = panel;

    this.nodes = [];   // {id,label,keywords,x,y,vx,vy,radius,...}
    this.edges = [];   // {source,target,weight,keywords,basis}
    this.nodeById = new Map();

    this.hoverNode = null;
    this.hoverEdge = null;
    this.selectedNode = null;
    this.dragNode = null;
    this.running = false;

    // 물리 파라미터
    this.repulsion = 22000;   // 노드 간 반발력
    this.springLength = 170;  // 엣지 기본 길이
    this.springK = 0.035;     // 엣지 장력
    this.damping = 0.85;      // 감쇠
    this.centerPull = 0.012;  // 중앙 인력

    this._bindEvents();
  }

  // ---------- 데이터 로드 ----------

  setData({ nodes, edges }) {
    const W = this.canvas.clientWidth || 900;
    const H = this.canvas.clientHeight || 600;
    this.nodes = nodes.map((n, i) => {
      const angle = (2 * Math.PI * i) / Math.max(nodes.length, 1);
      const r = Math.min(W, H) * 0.32;
      return {
        ...n,
        x: W / 2 + r * Math.cos(angle) + (Math.random() - 0.5) * 40,
        y: H / 2 + r * Math.sin(angle) + (Math.random() - 0.5) * 40,
        vx: 0, vy: 0,
        radius: Math.min(14 + Math.sqrt(n.chunk_count || 0) * 0.9, 30),
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

  // ---------- 시뮬레이션 루프 ----------

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

    // 반발력 (모든 쌍)
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
        const f = this.repulsion / d2;
        const d = Math.sqrt(d2);
        const fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx += fx; a.vy += fy;
        b.vx -= fx; b.vy -= fy;
      }
    }

    // 스프링 (엣지) — 유사도가 높을수록 더 가깝게
    for (const e of this.edges) {
      const a = this.nodeById.get(e.source), b = this.nodeById.get(e.target);
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const target = this.springLength * (1.35 - e.weight * 0.7);
      const f = this.springK * (d - target);
      const fx = (dx / d) * f, fy = (dy / d) * f;
      a.vx += fx; a.vy += fy;
      b.vx -= fx; b.vy -= fy;
    }

    // 중앙 인력 + 적분
    for (const n of nodes) {
      n.vx += (W / 2 - n.x) * this.centerPull;
      n.vy += (H / 2 - n.y) * this.centerPull;
      if (n === this.dragNode) { n.vx = 0; n.vy = 0; continue; }
      n.vx *= this.damping;
      n.vy *= this.damping;
      n.x += Math.max(-14, Math.min(14, n.vx));
      n.y += Math.max(-14, Math.min(14, n.vy));
      n.x = Math.max(n.radius + 4, Math.min(W - n.radius - 4, n.x));
      n.y = Math.max(n.radius + 4, Math.min(H - n.radius - 4, n.y));
    }
  }

  // ---------- 렌더링 ----------

  _draw() {
    const ctx = this.ctx;
    const W = this.canvas.clientWidth, H = this.canvas.clientHeight;
    ctx.clearRect(0, 0, W, H);

    if (!this.nodes.length) {
      ctx.fillStyle = "#7a7a7a";
      ctx.font = "15px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("표시할 문서가 없습니다. PDF를 먼저 업로드해주세요.", W / 2, H / 2);
      return;
    }

    const related = this._relatedSet();

    // ----- 엣지 -----
    for (const e of this.edges) {
      const a = this.nodeById.get(e.source), b = this.nodeById.get(e.target);
      const active = e === this.hoverEdge ||
        (this.selectedNode &&
          (e.source === this.selectedNode.id || e.target === this.selectedNode.id));
      const dimmed = (this.selectedNode || this.hoverNode) && !active &&
        !this._edgeTouches(e, this.hoverNode);

      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.lineWidth = 1 + e.weight * 5;
      ctx.strokeStyle = active
        ? "rgba(0, 102, 204, 0.85)"
        : dimmed ? "rgba(0, 0, 0, 0.05)" : "rgba(0, 0, 0, 0.16)";
      ctx.stroke();

      // 엣지 라벨: 활성 상태면 유사도 + 공유 키워드를 선 중앙에 표시
      if (active) {
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        const kw = (e.keywords || []).slice(0, 3).join(" · ");
        const label = `${Math.round(e.weight * 100)}%${kw ? "  " + kw : ""}`;
        ctx.font = "600 12px system-ui, sans-serif";
        const tw = ctx.measureText(label).width;
        ctx.fillStyle = "rgba(255,255,255,0.92)";
        this._roundRect(mx - tw / 2 - 7, my - 12, tw + 14, 22, 11);
        ctx.fill();
        ctx.strokeStyle = "rgba(0,102,204,0.4)";
        ctx.lineWidth = 1;
        this._roundRect(mx - tw / 2 - 7, my - 12, tw + 14, 22, 11);
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

      ctx.beginPath();
      ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
      ctx.fillStyle = dimmed ? "rgba(39,39,41,0.18)"
        : isSel || isHover ? "#0066cc" : "#272729";
      ctx.fill();
      if (isSel) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.radius + 4, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(0,102,204,0.35)";
        ctx.lineWidth = 3;
        ctx.stroke();
      }

      // 라벨 (항상 표시 — 연결 파악의 핵심)
      const label = this._shortLabel(n.label);
      ctx.font = `${isSel || isHover ? "600 " : ""}12px system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.fillStyle = dimmed ? "rgba(29,29,31,0.25)" : "#1d1d1f";
      ctx.fillText(label, n.x, n.y + n.radius + 16);
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
    const base = name.replace(/\.pdf$/i, "");
    return base.length > 18 ? base.slice(0, 17) + "…" : base;
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
          <p><strong>노드</strong> = 문서 · <strong>선</strong> = 내용이 비슷한 문서</p>
          <p>선이 <strong>굵을수록</strong> 더 강하게 연결된 것입니다.</p>
          <p>노드를 클릭하면 연결 근거(공유 키워드)를 보여드립니다.</p>
        </div>`;
      return;
    }

    const n = this.selectedNode;
    const connections = this.edges
      .filter((e) => e.source === n.id || e.target === n.id)
      .map((e) => ({
        other: this.nodeById.get(e.source === n.id ? e.target : e.source),
        weight: e.weight,
        keywords: e.keywords || [],
        basis: e.basis,
      }))
      .sort((a, b) => b.weight - a.weight);

    const connHtml = connections.length
      ? connections.map((c) => `
          <div class="graph-conn">
            <div class="graph-conn-head">
              <span class="graph-conn-name">${esc(this._shortLabel(c.other.label))}</span>
              <span class="score-badge">${Math.round(c.weight * 100)}%</span>
            </div>
            ${c.keywords.length
              ? `<div class="graph-conn-kw">공유 키워드: ${c.keywords.map((k) => `<span class="chip">${esc(k)}</span>`).join("")}</div>`
              : ""}
          </div>`).join("")
      : `<p class="graph-panel-muted">다른 문서와 뚜렷한 연결이 없습니다.</p>`;

    this.panel.innerHTML = `
      <h3 class="graph-panel-title">${esc(n.label)}</h3>
      <p class="graph-panel-muted">
        ${n.page_count}페이지 · 텍스트 ${n.chunk_count} · 이미지 ${n.image_count} · 표 ${n.table_count}
      </p>
      ${n.keywords?.length
        ? `<div class="graph-conn-kw" style="margin:8px 0 14px;">핵심 키워드: ${n.keywords.slice(0, 6).map((k) => `<span class="chip">${esc(k)}</span>`).join("")}</div>`
        : ""}
      <h4 class="graph-panel-sub">연결된 문서 (${connections.length})</h4>
      ${connHtml}
      <button class="button-primary graph-panel-btn" onclick="openSummary(${n.id})">문서 요약 보기</button>
    `;
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
    let best = null, bestDist = 8; // 8px 이내
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
      if (this.dragNode) {
        this.dragNode.x = x;
        this.dragNode.y = y;
        return;
      }
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

// ===== 그래프 뷰 컨트롤러 =====

class GraphView {
  constructor() {
    this.graph = null;
    this.loaded = false;
  }

  async load(force = false) {
    const canvas = document.querySelector("#graph-canvas");
    const panel = document.querySelector("#graph-panel");
    const statusEl = document.querySelector("#graph-status");
    if (!canvas) return;

    if (!this.graph) this.graph = new KnowledgeGraph(canvas, panel);
    if (this.loaded && !force) { this.graph.start(); return; }

    const threshold = document.querySelector("#graph-threshold")?.value ?? 0.35;
    statusEl.textContent = "그래프 계산 중…";
    try {
      const res = await fetch(`/api/graph?threshold=${threshold}`);
      if (!res.ok) throw new Error("서버 오류");
      const data = await res.json();
      this.graph.setData(data);
      this.loaded = true;
      statusEl.textContent =
        `문서 ${data.nodes.length}개 · 연결 ${data.edges.length}개`;
    } catch {
      statusEl.textContent = "그래프를 불러올 수 없습니다.";
    }
  }
}

const graphView = new GraphView();
