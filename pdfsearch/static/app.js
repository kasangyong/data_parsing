/* PDF 멀티모달 검색 엔진 - 프론트엔드 */

// ===== 유틸 =====
const $ = (sel) => document.querySelector(sel);

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

const TYPE_LABELS = {
  text: "텍스트",
  image: "이미지",
  table: "표",
  annotation: "주석",
  outline: "목차",
};

// ===== 뷰 전환 =====
function showView(name) {
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  $(`#view-${name}`).classList.remove("hidden");
  document.querySelectorAll(".nav-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === name)
  );
  if (name === "documents") loadDocuments();
  if (name === "graph") graphView.load();
  if (name === "entities") entityGraphView.load();
}

// ===== 모델 상태 =====
async function checkStatus() {
  const el = $("#model-status");
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    if (data.models.all_ready) {
      el.textContent = "모델 준비됨";
      el.className = "model-status ready";
    } else {
      el.textContent = "모델 미다운로드";
      el.className = "model-status not-ready";
      el.title = "터미널에서 python download_models.py 를 실행해주세요.";
    }
  } catch {
    el.textContent = "서버 연결 실패";
    el.className = "model-status not-ready";
  }
}

function modelNotReadyBanner() {
  return `<div class="alert-banner">
    임베딩 모델이 아직 다운로드되지 않았습니다.<br>
    터미널에서 <code>python download_models.py</code> 를 실행한 뒤 서버를 재시작해주세요.
  </div>`;
}

// ===== 검색 =====
$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = $("#search-input").value.trim();
  if (!query) return;
  const type = $("#search-type").value;
  const resultsEl = $("#search-results");
  resultsEl.innerHTML = `<div class="spinner">검색 중...</div>`;

  try {
    const res = await fetch(
      `/api/search?q=${encodeURIComponent(query)}&type=${type}`
    );
    if (res.status === 503) {
      resultsEl.innerHTML = modelNotReadyBanner();
      return;
    }
    if (!res.ok) {
      const err = await res.json();
      resultsEl.innerHTML = `<div class="empty-state">오류: ${escapeHtml(err.detail || "검색 실패")}</div>`;
      return;
    }
    const data = await res.json();
    renderSearchResults(data);
  } catch (err) {
    resultsEl.innerHTML = `<div class="empty-state">서버에 연결할 수 없습니다.</div>`;
  }
});

function renderSearchResults(data) {
  const resultsEl = $("#search-results");
  if (!data.results.length) {
    resultsEl.innerHTML = `<div class="empty-state">검색 결과가 없습니다.<br>PDF를 먼저 업로드해보세요.</div>`;
    return;
  }

  resultsEl.innerHTML = data.results
    .map((r) => {
      const matchesHtml = r.matches
        .map((m) => {
          let content;
          if (m.match_type === "image") {
            content = `<img src="${m.preview}" alt="매칭 이미지" loading="lazy" />`;
          } else {
            content = escapeHtml(m.preview);
          }
          return `<div class="match-item">
            <span class="page-tag">p.${m.page_number}</span>
            <span class="type-badge type-${m.match_type}">${TYPE_LABELS[m.match_type]}</span>
            <div>${content}</div>
          </div>`;
        })
        .join("");

      return `<div class="result-card" onclick="openSummary(${r.document_id})">
        <div class="result-head">
          <span class="result-title">${escapeHtml(r.filename)}</span>
          <span class="score-badge">유사도 ${(r.score * 100).toFixed(1)}%</span>
          <span class="type-badge type-${r.match_type}">최고 매칭: ${TYPE_LABELS[r.match_type]}</span>
        </div>
        <div class="match-list">${matchesHtml}</div>
      </div>`;
    })
    .join("");
}

// ===== 업로드 =====
const dropZone = $("#drop-zone");
const fileInput = $("#file-input");

dropZone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  if (fileInput.files.length) uploadFile(fileInput.files[0]);
});

["dragover", "dragenter"].forEach((ev) =>
  dropZone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropZone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((ev) =>
  dropZone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
  })
);
dropZone.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
});

async function uploadFile(file) {
  const statusEl = $("#upload-status");
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    statusEl.innerHTML = `<div class="upload-result error">PDF 파일만 업로드할 수 있습니다.</div>`;
    return;
  }
  statusEl.innerHTML = `<div class="spinner">업로드 및 파싱/인덱싱 중... (파일 크기에 따라 시간이 걸릴 수 있어요)</div>`;

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch("/api/upload", { method: "POST", body: formData });
    const data = await res.json();

    if (res.status === 503) {
      statusEl.innerHTML = modelNotReadyBanner();
      return;
    }
    if (!res.ok) {
      statusEl.innerHTML = `<div class="upload-result error">${escapeHtml(data.detail || "업로드 실패")}</div>`;
      return;
    }

    const warnings = data.warnings?.length
      ? `<p class="doc-meta" style="margin-top:8px;">경고 ${data.warnings.length}건 (일부 요소 추출 실패)</p>`
      : "";

    const ex = data.extracted;
    const chips = [
      `<span class="chip">텍스트 청크 ${ex.text_chunks}개</span>`,
      `<span class="chip">이미지 ${ex.images}개</span>`,
    ];
    if (ex.vector_graphics) chips.push(`<span class="chip">벡터그래픽 ${ex.vector_graphics}개</span>`);
    chips.push(`<span class="chip">표 ${ex.tables}개</span>`);
    if (ex.outlines) chips.push(`<span class="chip">목차 ${ex.outlines}개</span>`);
    if (ex.links) chips.push(`<span class="chip">링크 ${ex.links}개</span>`);
    if (ex.annotations) chips.push(`<span class="chip">주석 ${ex.annotations}개</span>`);
    if (data.ocr_pages?.length) chips.push(`<span class="chip">OCR ${data.ocr_pages.length}페이지</span>`);

    statusEl.innerHTML = `<div class="upload-result success">
      <strong>${escapeHtml(data.filename)}</strong> 업로드 완료 (${data.page_count}페이지)
      <div class="stat-chips">${chips.join("")}</div>
      ${warnings}
    </div>`;
    fileInput.value = "";
  } catch {
    statusEl.innerHTML = `<div class="upload-result error">서버에 연결할 수 없습니다.</div>`;
  }
}

// ===== 문서 목록 =====
async function loadDocuments() {
  const listEl = $("#documents-list");
  listEl.innerHTML = `<div class="spinner">불러오는 중...</div>`;
  try {
    const res = await fetch("/api/documents");
    const data = await res.json();
    if (!data.documents.length) {
      listEl.innerHTML = `<div class="empty-state">업로드된 문서가 없습니다.</div>`;
      return;
    }
    listEl.innerHTML = data.documents
      .map(
        (d) => `<div class="doc-card">
          <div class="doc-info" onclick="openSummary(${d.id})">
            <div class="doc-name">${escapeHtml(d.filename)}</div>
            <div class="doc-meta">
              ${d.page_count}페이지 · 텍스트 ${d.chunk_count} · 이미지 ${d.image_count} · 표 ${d.table_count}
              · ${escapeHtml(d.created_at)}
            </div>
          </div>
          <button class="delete-btn" onclick="deleteDocument(${d.id}, event)">삭제</button>
        </div>`
      )
      .join("");
  } catch {
    listEl.innerHTML = `<div class="empty-state">서버에 연결할 수 없습니다.</div>`;
  }
}

async function deleteDocument(id, event) {
  event.stopPropagation();
  if (!confirm("이 문서를 삭제할까요?")) return;
  try {
    const res = await fetch(`/api/documents/${id}`, { method: "DELETE" });
    if (res.ok) loadDocuments();
    else alert("삭제 실패");
  } catch {
    alert("서버에 연결할 수 없습니다.");
  }
}

// ===== 문서 요약 =====
async function openSummary(documentId) {
  showView("summary");
  const el = $("#summary-content");
  el.innerHTML = `<div class="spinner">요약 생성 중... (문서 크기에 따라 시간이 걸릴 수 있어요)</div>`;

  try {
    const res = await fetch(`/api/documents/${documentId}/summary`);
    if (res.status === 503) {
      el.innerHTML = modelNotReadyBanner();
      return;
    }
    if (!res.ok) {
      const err = await res.json();
      el.innerHTML = `<div class="empty-state">오류: ${escapeHtml(err.detail || "요약 실패")}</div>`;
      return;
    }
    const data = await res.json();
    renderSummary(data);
  } catch {
    el.innerHTML = `<div class="empty-state">서버에 연결할 수 없습니다.</div>`;
  }
}

function renderSummary(data) {
  const el = $("#summary-content");
  const doc = data.document;
  const meta = doc.metadata || {};

  // 핵심 문장
  const sentencesHtml = data.key_sentences.length
    ? data.key_sentences
        .map(
          (s) => `<div class="key-sentence">
            ${escapeHtml(s.sentence)}
            <span class="page-tag">— p.${s.page_number}</span>
          </div>`
        )
        .join("")
    : `<div class="empty-state">추출된 텍스트가 없습니다.</div>`;

  // 표
  const tablesHtml = data.tables.length
    ? data.tables
        .map((t) => {
          const rows = t.preview
            .map((row, i) => {
              const tag = i === 0 ? "th" : "td";
              const cells = row
                .map((c) => `<${tag}>${escapeHtml(c)}</${tag}>`)
                .join("");
              return `<tr>${cells}</tr>`;
            })
            .join("");
          const more =
            t.rows > t.preview.length
              ? `<div class="table-caption">… 외 ${t.rows - t.preview.length}행</div>`
              : "";
          return `<div class="table-preview">
            <div class="table-caption">p.${t.page_number} · ${t.rows}행 × ${t.cols}열</div>
            <table>${rows}</table>
            ${more}
          </div>`;
        })
        .join("")
    : `<div class="empty-state">추출된 표가 없습니다.</div>`;

  // 이미지 (임베디드 + 벡터 그래픽)
  const imagesHtml = data.images.length
    ? `<div class="image-gallery">` +
      data.images
        .map(
          (img) => `<div class="gallery-item">
            <a href="${img.url}" target="_blank"><img src="${img.url}" loading="lazy" alt="p.${img.page_number} 이미지" /></a>
            <span class="page-tag">p.${img.page_number}${img.kind === "vector" ? " · 벡터" : ""}</span>
          </div>`
        )
        .join("") +
      `</div>`
    : `<div class="empty-state">추출된 이미지가 없습니다.</div>`;

  // 메타데이터
  const metaEntries = Object.entries(meta).filter(([, v]) => v);
  const metaHtml = metaEntries.length
    ? `<div class="doc-meta" style="margin-top:6px;">${metaEntries
        .map(([k, v]) => `<strong>${escapeHtml(k)}</strong>: ${escapeHtml(String(v))}`)
        .join(" · ")}</div>`
    : "";

  // 목차
  const outlinesHtml = (data.outlines || []).length
    ? data.outlines
        .map(
          (o) => `<div class="outline-item" style="padding-left:${(o.level - 1) * 20}px;">
            ${escapeHtml(o.title)} <span class="page-tag">p.${o.page_number}</span>
          </div>`
        )
        .join("")
    : "";

  // 링크
  const linksHtml = (data.links || []).length
    ? data.links
        .map(
          (l) => `<div class="link-item">
            <span class="page-tag">p.${l.page_number}</span>
            <a href="${escapeHtml(l.url)}" target="_blank" rel="noopener">${escapeHtml(l.url)}</a>
            ${l.anchor_text ? `<span class="doc-meta">(${escapeHtml(l.anchor_text)})</span>` : ""}
          </div>`
        )
        .join("")
    : "";

  // 주석
  const annotationsHtml = (data.annotations || []).length
    ? data.annotations
        .map(
          (a) => `<div class="key-sentence annotation">
            <span class="type-badge type-annotation">${escapeHtml(a.type)}</span>
            ${escapeHtml(a.content)}
            <span class="page-tag">— p.${a.page_number}</span>
          </div>`
        )
        .join("")
    : "";

  const s = data.stats;
  const statChips = [
    `<span class="chip">텍스트 청크 ${s.chunk_count}개</span>`,
    `<span class="chip">이미지 ${s.image_count}개</span>`,
    `<span class="chip">표 ${s.table_count}개</span>`,
  ];
  if (s.ocr_chunk_count) statChips.push(`<span class="chip">OCR 청크 ${s.ocr_chunk_count}개</span>`);
  if (s.outline_count) statChips.push(`<span class="chip">목차 ${s.outline_count}개</span>`);
  if (s.link_count) statChips.push(`<span class="chip">링크 ${s.link_count}개</span>`);
  if (s.annotation_count) statChips.push(`<span class="chip">주석 ${s.annotation_count}개</span>`);

  const optionalSections = [];
  if (outlinesHtml) {
    optionalSections.push(`<div class="summary-section"><h3>목차</h3>${outlinesHtml}</div>`);
  }
  if (annotationsHtml) {
    optionalSections.push(`<div class="summary-section"><h3>주석 (${s.annotation_count}개)</h3>${annotationsHtml}</div>`);
  }
  if (linksHtml) {
    optionalSections.push(`<div class="summary-section"><h3>링크 (${s.link_count}개)</h3>${linksHtml}</div>`);
  }

  el.innerHTML = `
    <div class="summary-header">
      <h2>${escapeHtml(doc.filename)}</h2>
      <div class="doc-meta">
        ${doc.page_count}페이지 · 업로드: ${escapeHtml(doc.created_at)}
        · <a href="${doc.pdf_url}" target="_blank">원본 PDF 열기</a>
      </div>
      ${metaHtml}
      <div class="stat-chips" style="margin-top:10px;">${statChips.join("")}</div>
    </div>

    <div class="summary-section">
      <h3>핵심 요약</h3>
      ${sentencesHtml}
    </div>

    ${optionalSections.join("")}

    <div class="summary-section">
      <h3>표 (${s.table_count}개)</h3>
      ${tablesHtml}
    </div>

    <div class="summary-section">
      <h3>이미지 (${s.image_count}개)</h3>
      ${imagesHtml}
    </div>
  `;
}

// ===== 초기화 =====
checkStatus();
showView("search");
