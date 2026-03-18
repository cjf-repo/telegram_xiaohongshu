const state = {
  page: 1,
  pageSize: 20,
  totalPages: 1,
  total: 0,
};

const els = {
  filterForm: document.getElementById("filterForm"),
  chatId: document.getElementById("chatId"),
  keyword: document.getElementById("keyword"),
  messageId: document.getElementById("messageId"),
  hasMedia: document.getElementById("hasMedia"),
  dateFrom: document.getElementById("dateFrom"),
  dateTo: document.getElementById("dateTo"),
  includeSeparator: document.getElementById("includeSeparator"),
  summary: document.getElementById("summary"),
  result: document.getElementById("result"),
  prevPage: document.getElementById("prevPage"),
  nextPage: document.getElementById("nextPage"),
  pageInfo: document.getElementById("pageInfo"),
};

function escapeHtml(text) {
  if (!text) return "";
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function truncate(text, max = 180) {
  if (!text) return "";
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function buildQuery() {
  const params = new URLSearchParams();
  if (els.chatId.value) params.set("chat_id", els.chatId.value);
  if (els.keyword.value.trim()) params.set("keyword", els.keyword.value.trim());
  if (els.messageId.value.trim()) params.set("message_id", els.messageId.value.trim());
  if (els.hasMedia.value) params.set("has_media", els.hasMedia.value);
  if (els.dateFrom.value) params.set("date_from", els.dateFrom.value);
  if (els.dateTo.value) params.set("date_to", els.dateTo.value);
  params.set("include_separator", String(els.includeSeparator.checked));
  params.set("page", String(state.page));
  params.set("page_size", String(state.pageSize));
  return params.toString();
}

async function loadChats() {
  const res = await fetch("/api/chats");
  const data = await res.json();
  const items = data.items || [];
  for (const item of items) {
    const option = document.createElement("option");
    option.value = String(item.chat_id);
    option.textContent = `${item.chat_id} (${item.total_messages})`;
    els.chatId.appendChild(option);
  }
}

function mediaPreview(item) {
  if (!item.saved_file_path) {
    return `<div class="media-meta">未保存本地文件</div>`;
  }
  const encoded = encodeURIComponent(item.saved_file_path);
  const mediaUrl = `/api/media?path=${encoded}`;
  const type = (item.media_type || "").toLowerCase();

  if (type === "photo") {
    return `<img loading="lazy" src="${mediaUrl}" alt="photo" />`;
  }
  if (type === "video") {
    return `<video controls preload="metadata" src="${mediaUrl}"></video>`;
  }
  return `<a target="_blank" href="${mediaUrl}">打开文件</a>`;
}

function renderGroups(items) {
  if (!items || items.length === 0) {
    els.result.innerHTML = '<section class="panel empty-text">暂无数据</section>';
    return;
  }

  const html = items
    .map((group) => {
      const head = `
        <div class="group-head">
          <span><strong>chat_id:</strong> ${escapeHtml(String(group.chat_id))}</span>
          <span><strong>anchor:</strong> ${group.anchor_message_id}</span>
          <span><strong>first_msg_id:</strong> ${group.first_message_id}</span>
          <span><strong>last_msg_id:</strong> ${group.latest_message_id}</span>
          <span><strong>caption_time:</strong> ${escapeHtml(group.caption_message_date || "-")}</span>
          <span><strong>latest:</strong> ${escapeHtml(group.latest_message_date || "-")}</span>
          <span><strong>total_msg:</strong> ${group.total_messages || 0}</span>
          <span><strong>text:</strong> ${group.text_messages.length}</span>
          <span><strong>media:</strong> ${group.media_items.length}</span>
          <span><strong>separator:</strong> ${group.separator_count || 0}</span>
        </div>
      `;

      const text = group.primary_text
        ? `<p class="primary-text">${escapeHtml(truncate(group.primary_text, 400))}</p>`
        : '<p class="primary-text empty-text">该分组无 caption（可能是首个 caption 之前的数据）</p>';

      const idList = Array.isArray(group.message_ids) ? group.message_ids : [];
      const idPreview = idList.length > 40
        ? `${idList.slice(0, 40).join(", ")} ...`
        : idList.join(", ");
      const msgIdsBlock = `
        <p class="group-msg-ids">
          <strong>message_ids:</strong> ${escapeHtml(idPreview || "-")}
        </p>
      `;

      const mediaList = (group.media_items || [])
        .map((item) => {
          const separatorTag = item.is_separator
            ? '<span class="tag separator">separator</span>'
            : "";
          return `
            <article class="media-item">
              ${mediaPreview(item)}
              <div class="media-meta">
                <span class="tag">${escapeHtml(item.media_type || "unknown")}</span>
                ${separatorTag}
                <div>msg_id: ${item.message_id}</div>
                <div>${escapeHtml(item.original_file_name || "")}</div>
                <div>${escapeHtml(item.saved_file_path || "")}</div>
              </div>
            </article>
          `;
        })
        .join("");

      return `
        <section class="group-card">
          ${head}
          <div class="group-body">
            ${text}
            ${msgIdsBlock}
            <div class="media-strip">${mediaList || '<div class="empty-text">无媒体</div>'}</div>
          </div>
        </section>
      `;
    })
    .join("");

  els.result.innerHTML = html;
}

function updatePager() {
  els.pageInfo.textContent = `第 ${state.page} / ${state.totalPages} 页，共 ${state.total} 组`;
  els.prevPage.disabled = state.page <= 1;
  els.nextPage.disabled = state.page >= state.totalPages;
}

async function search() {
  els.summary.textContent = "查询中...";
  const query = buildQuery();
  const res = await fetch(`/api/groups?${query}`);
  const data = await res.json();

  const pagination = data.pagination || {};
  state.total = Number(pagination.total || 0);
  state.totalPages = Number(pagination.total_pages || 1);
  if (state.totalPages <= 0) state.totalPages = 1;

  els.summary.textContent = `筛选结果：${state.total} 组（每页 ${state.pageSize}）`;
  renderGroups(data.items || []);
  updatePager();
}

els.filterForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  state.page = 1;
  await search();
});

els.prevPage.addEventListener("click", async () => {
  if (state.page <= 1) return;
  state.page -= 1;
  await search();
});

els.nextPage.addEventListener("click", async () => {
  if (state.page >= state.totalPages) return;
  state.page += 1;
  await search();
});

async function init() {
  await loadChats();
  await search();
}

init().catch((err) => {
  els.summary.textContent = `初始化失败: ${err.message}`;
});
