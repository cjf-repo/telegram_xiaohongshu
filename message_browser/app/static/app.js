const state = {
  page: 1,
  pageSize: 20,
  totalPages: 1,
  total: 0,
  selectedGroupKeys: new Set(),
  aiResult: null,
};
const XHS_TITLE_MAX = 20;

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
  productUrl: document.getElementById("productUrl"),
  publishTitle: document.getElementById("publishTitle"),
  publishDescription: document.getElementById("publishDescription"),
  publishIncludeSeparator: document.getElementById("publishIncludeSeparator"),
  selectedInfo: document.getElementById("selectedInfo"),
  previewPublishBtn: document.getElementById("previewPublishBtn"),
  publishBtn: document.getElementById("publishBtn"),
  clearSelectedBtn: document.getElementById("clearSelectedBtn"),
  publishPreviewBody: document.getElementById("publishPreviewBody"),
  xhsStatus: document.getElementById("xhsStatus"),
  checkXhsBtn: document.getElementById("checkXhsBtn"),
  aiPrompt: document.getElementById("aiPrompt"),
  aiUseVision: document.getElementById("aiUseVision"),
  aiMaxImages: document.getElementById("aiMaxImages"),
  aiTemperature: document.getElementById("aiTemperature"),
  aiStatus: document.getElementById("aiStatus"),
  checkAiBtn: document.getElementById("checkAiBtn"),
  aiGenerateBtn: document.getElementById("aiGenerateBtn"),
  aiApplyBtn: document.getElementById("aiApplyBtn"),
  aiResultBody: document.getElementById("aiResultBody"),
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

function isValidHttpUrl(text) {
  try {
    const u = new URL(text);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

function makeGroupKey(chatId, anchorMessageId) {
  return `${chatId}::${anchorMessageId}`;
}

function parseGroupKey(key) {
  const idx = key.lastIndexOf("::");
  if (idx < 0) return null;
  const chatId = key.slice(0, idx);
  const anchorMessageId = Number(key.slice(idx + 2));
  if (!chatId || Number.isNaN(anchorMessageId)) return null;
  return { chat_id: chatId, anchor_message_id: anchorMessageId };
}

function selectedGroupList() {
  const result = [];
  for (const key of state.selectedGroupKeys) {
    const parsed = parseGroupKey(key);
    if (parsed) result.push(parsed);
  }
  return result;
}

function updateSelectedInfo() {
  els.selectedInfo.textContent = `已选分组: ${state.selectedGroupKeys.size}`;
}

function normalizePublishTitle(text) {
  const raw = (text || "").replace(/\s+/g, " ").trim();
  if (!raw) return "";
  return raw.slice(0, XHS_TITLE_MAX);
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

async function checkXhsStatus() {
  els.xhsStatus.textContent = "XHS状态: 检查中...";
  try {
    const res = await fetch("/api/xhs/status");
    const data = await res.json();
    const mode = data.mode || "-";
    const ok = !!data.ok;
    const message = data.message || "-";
    const loginCommand = data.login_command ? `，登录命令: ${data.login_command}` : "";
    els.xhsStatus.textContent = `XHS状态(${mode}): ${ok ? "就绪" : "未就绪"} - ${message}${loginCommand}`;
  } catch (err) {
    els.xhsStatus.textContent = `XHS状态: 检查失败 - ${err.message || err}`;
  }
}

async function checkAiStatus() {
  els.aiStatus.textContent = "AI状态: 检查中...";
  try {
    const res = await fetch("/api/ai/status");
    const data = await res.json();
    const ready = !!data.ready;
    const modelPart = `text=${data.text_model || "-"}, vision=${data.vision_model || "-"}`;
    els.aiStatus.textContent = `AI状态: ${ready ? "就绪" : "未就绪"} - ${data.message || "-"} (${modelPart})`;
  } catch (err) {
    els.aiStatus.textContent = `AI状态: 检查失败 - ${err.message || err}`;
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

function renderPublishPreview(payload) {
  if (!payload) {
    els.publishPreviewBody.innerHTML = '<div class="empty-text">尚未生成预览</div>';
    return;
  }

  const title = escapeHtml(payload.title || "-");
  const description = escapeHtml(truncate(payload.description || "", 1200));
  const assets = Array.isArray(payload.media_assets) ? payload.media_assets : [];
  const mediaHtml = assets.map((asset) => {
    const path = asset.saved_file_path || "";
    const encoded = encodeURIComponent(path);
    const mediaUrl = `/api/media?path=${encoded}`;
    const mediaType = (asset.media_type || "").toLowerCase();
    const preview = mediaType === "video"
      ? `<video controls preload="metadata" src="${mediaUrl}"></video>`
      : `<img loading="lazy" src="${mediaUrl}" alt="preview" />`;
    return `
      <article class="publish-preview-item">
        ${preview}
        <div class="publish-preview-meta">
          <div>msg_id: ${asset.message_id || "-"}</div>
          <div>${escapeHtml(asset.original_file_name || "")}</div>
        </div>
      </article>
    `;
  }).join("");

  els.publishPreviewBody.innerHTML = `
    <p class="publish-preview-title">${title}</p>
    <p class="publish-preview-desc">${description || "<span class='empty-text'>无描述</span>"}</p>
    <p class="group-msg-ids">
      <strong>统计:</strong>
      分组 ${payload.group_count || 0}，
      消息 ${payload.message_id_count || 0}，
      媒体 ${payload.media_count || 0}
    </p>
    <div class="publish-preview-grid">${mediaHtml || '<div class="empty-text">无媒体</div>'}</div>
  `;
}

function renderAICopyResult(result) {
  state.aiResult = result || null;
  if (!result) {
    els.aiResultBody.innerHTML = '<div class="empty-text">尚未生成AI文案</div>';
    return;
  }
  const title = escapeHtml(result.title || "-");
  const content = escapeHtml(result.content || "");
  const highlights = Array.isArray(result.highlights) ? result.highlights : [];
  const hashtags = Array.isArray(result.hashtags) ? result.hashtags : [];
  const pricing = result.pricing && typeof result.pricing === "object" ? result.pricing : {};
  const strategy = result.strategy && typeof result.strategy === "object" ? result.strategy : {};
  const extraTitles = Array.isArray(result.titles) ? result.titles : [];
  const highlightsHtml = highlights.length
    ? `<p><strong>卖点:</strong> ${escapeHtml(highlights.join(" / "))}</p>`
    : "";
  const hashtagsHtml = hashtags.length
    ? `<p><strong>标签:</strong> ${escapeHtml(hashtags.join(" "))}</p>`
    : "";
  const pricingHtml = (pricing.recommended_price || pricing.event_price || pricing.pricing_note)
    ? `<p><strong>建议售价:</strong> ${escapeHtml(String(pricing.recommended_price || "-"))}，<strong>活动价:</strong> ${escapeHtml(String(pricing.event_price || "-"))}</p>
       <p><strong>定价说明:</strong> ${escapeHtml(String(pricing.pricing_note || "-"))}</p>`
    : "";
  const strategyHtml = (strategy.main_style || strategy.framework || strategy.reason)
    ? `<p><strong>主风格:</strong> ${escapeHtml(String(strategy.main_style || "-"))}，<strong>框架:</strong> ${escapeHtml(String(strategy.framework || "-"))}</p>
       <p><strong>策略说明:</strong> ${escapeHtml(String(strategy.reason || "-"))}</p>`
    : "";
  const extraTitlesHtml = extraTitles.length
    ? `<p><strong>备选标题:</strong> ${escapeHtml(extraTitles.join(" ｜ "))}</p>`
    : "";
  const meta = `模型: ${escapeHtml(result.model || "-")} | 视觉: ${result.used_vision ? "是" : "否"} | 图片: ${result.used_image_count || 0}`;
  els.aiResultBody.innerHTML = `
    <p><strong>标题建议:</strong> ${title}</p>
    <p><strong>文案建议:</strong></p>
    <p>${content || "<span class='empty-text'>无内容</span>"}</p>
    ${highlightsHtml}
    ${hashtagsHtml}
    ${pricingHtml}
    ${strategyHtml}
    ${extraTitlesHtml}
    <p class="empty-text">${meta}</p>
  `;
}

function renderGroups(items) {
  if (!items || items.length === 0) {
    els.result.innerHTML = '<section class="panel empty-text">暂无数据</section>';
    return;
  }

  const html = items
    .map((group) => {
      const key = makeGroupKey(String(group.chat_id), Number(group.anchor_message_id));
      const encodedKey = encodeURIComponent(key);
      const checked = state.selectedGroupKeys.has(key) ? "checked" : "";
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
          <label class="group-select-label">
            <input class="group-select" type="checkbox" data-key="${encodedKey}" ${checked} />
            选中上架
          </label>
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
  updateSelectedInfo();
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

async function requestPublish(apiPath) {
  const groups = selectedGroupList();
  if (groups.length === 0) {
    els.summary.textContent = "请先勾选至少一个分组";
    alert("请先勾选至少一个分组");
    return;
  }
  const productUrl = (els.productUrl.value || "").trim();
  if (productUrl && !isValidHttpUrl(productUrl)) {
    els.summary.textContent = "商品链接格式错误，请使用 http/https 完整链接";
    alert("商品链接格式错误，请使用 http/https 完整链接");
    return;
  }
  const normalizedTitle = normalizePublishTitle(els.publishTitle.value || "");
  if (normalizedTitle !== (els.publishTitle.value || "").trim()) {
    els.publishTitle.value = normalizedTitle;
  }
  const body = {
    groups,
    product_url: productUrl || null,
    title: normalizedTitle || null,
    description: (els.publishDescription.value || "").trim() || null,
    include_separator: !!els.publishIncludeSeparator.checked,
  };

  const res = await fetch(apiPath, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const raw = await res.text();
  let data = {};
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch {
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}: ${raw.slice(0, 300) || "返回非JSON"}`);
    }
    data = {};
  }
  if (!res.ok) {
    const detail = (data && data.detail) || "请求失败";
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

async function requestAICopy() {
  const groups = selectedGroupList();
  if (groups.length === 0) {
    els.summary.textContent = "请先勾选至少一个分组";
    alert("请先勾选至少一个分组");
    return;
  }

  const maxImagesNum = Number(els.aiMaxImages.value);
  const temperatureNum = Number(els.aiTemperature.value);
  const body = {
    groups,
    include_separator: !!els.publishIncludeSeparator.checked,
    prompt: (els.aiPrompt.value || "").trim() || null,
    use_vision: !!els.aiUseVision.checked,
    max_images: Number.isFinite(maxImagesNum) ? maxImagesNum : null,
    temperature: Number.isFinite(temperatureNum) ? temperatureNum : null,
  };

  const res = await fetch("/api/ai/copy/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const raw = await res.text();
  let data = {};
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch {
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}: ${raw.slice(0, 300) || "返回非JSON"}`);
    }
    data = {};
  }
  if (!res.ok) {
    const detail = (data && data.detail) || "AI请求失败";
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
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

els.result.addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLInputElement)) return;
  if (!target.classList.contains("group-select")) return;
  const encoded = target.dataset.key || "";
  const key = decodeURIComponent(encoded);
  if (target.checked) {
    state.selectedGroupKeys.add(key);
  } else {
    state.selectedGroupKeys.delete(key);
  }
  updateSelectedInfo();
});

els.clearSelectedBtn.addEventListener("click", () => {
  state.selectedGroupKeys.clear();
  updateSelectedInfo();
  renderPublishPreview(null);
  renderAICopyResult(null);
  els.summary.textContent = "已清空选择和预览";
  const checkboxes = els.result.querySelectorAll(".group-select");
  for (const checkbox of checkboxes) {
    checkbox.checked = false;
  }
});

els.previewPublishBtn.addEventListener("click", async () => {
  els.summary.textContent = "正在生成融合预览...";
  try {
    const data = await requestPublish("/api/xhs/publish/preview");
    if (!data) return;
    const payload = data.payload || {};
    els.summary.textContent = `预览完成：分组 ${payload.group_count || 0}，消息 ${payload.message_id_count || 0}，媒体 ${payload.media_count || 0}，标题: ${payload.title || "-"}`;
    renderPublishPreview(payload);
  } catch (err) {
    console.error(err);
    els.summary.textContent = `预览失败: ${err.message || err}`;
    renderPublishPreview(null);
    alert(`预览失败: ${err.message}`);
  }
});

els.publishBtn.addEventListener("click", async () => {
  els.summary.textContent = "正在提交上架...";
  try {
    const data = await requestPublish("/api/xhs/publish");
    if (!data) return;
    const summary = data.summary || {};
    els.summary.textContent = `上架已提交：分组 ${summary.group_count || 0}，消息 ${summary.message_id_count || 0}，媒体 ${summary.media_count || 0}，标题: ${summary.title || "-"}`;
    const result = data.publish_result || {};
    if (result.saved_file) {
      alert(`已生成上架文件: ${result.saved_file}`);
    } else if (result.note_url) {
      alert(`发布成功: ${result.note_url}`);
    } else {
      alert("上架请求已发送");
    }
  } catch (err) {
    console.error(err);
    els.summary.textContent = `上架失败: ${err.message || err}`;
    alert(`上架失败: ${err.message}`);
  }
});

els.checkXhsBtn.addEventListener("click", async () => {
  await checkXhsStatus();
});

els.checkAiBtn.addEventListener("click", async () => {
  await checkAiStatus();
});

els.aiGenerateBtn.addEventListener("click", async () => {
  els.summary.textContent = "正在生成AI文案...";
  try {
    const data = await requestAICopy();
    if (!data) return;
    const result = data.result || {};
    const summary = data.source_summary || {};
    els.summary.textContent = `AI文案生成完成：分组 ${summary.group_count || 0}，消息 ${summary.message_id_count || 0}，媒体 ${summary.media_count || 0}`;
    renderAICopyResult(result);
  } catch (err) {
    console.error(err);
    els.summary.textContent = `AI文案生成失败: ${err.message || err}`;
    renderAICopyResult(null);
    alert(`AI文案生成失败: ${err.message}`);
  }
});

els.aiApplyBtn.addEventListener("click", () => {
  if (!state.aiResult) {
    alert("请先生成AI文案");
    return;
  }
  const aiTitle = (state.aiResult.title || "").trim();
  const aiContent = (state.aiResult.content || "").trim();
  if (aiTitle) els.publishTitle.value = normalizePublishTitle(aiTitle);
  if (aiContent) els.publishDescription.value = aiContent;
  els.summary.textContent = "已将AI文案应用到上架输入框，可继续手动微调";
});

els.publishTitle.addEventListener("input", () => {
  const current = els.publishTitle.value || "";
  const normalized = normalizePublishTitle(current);
  if (current !== normalized) {
    els.publishTitle.value = normalized;
    els.summary.textContent = `发布标题最多 ${XHS_TITLE_MAX} 字，已自动截断`;
  }
});

async function init() {
  await loadChats();
  updateSelectedInfo();
  renderPublishPreview(null);
  renderAICopyResult(null);
  await checkXhsStatus();
  await checkAiStatus();
  await search();
}

init().catch((err) => {
  els.summary.textContent = `初始化失败: ${err.message}`;
});
