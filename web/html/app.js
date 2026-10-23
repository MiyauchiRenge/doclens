/* 内网文件预览 - 前端逻辑（零外部依赖，无 CDN） */
(function () {
  "use strict";

  var state = { sid: null, key: null, info: null, poll: null, closed: false };

  var el = {
    drop: document.getElementById("drop"),
    picker: document.getElementById("picker"),
    panel: document.getElementById("panel"),
    fname: document.getElementById("fname"),
    fsize: document.getElementById("fsize"),
    status: document.getElementById("status"),
    viewer: document.getElementById("viewer"),
    frame: document.getElementById("frame"),
    image: document.getElementById("image"),
    ofdHost: document.getElementById("ofdHost"),
    archiveHost: document.getElementById("archiveHost"),
    btnPreview: document.getElementById("btnPreview"),
    btnConvert: document.getElementById("btnConvert"),
    btnOcr: document.getElementById("btnOcr"),
    btnOcrPdf: document.getElementById("btnOcrPdf"),
    btnPdf: document.getElementById("btnPdf"),
    btnClose: document.getElementById("btnClose")
  };

  function fmtSize(n) {
    if (!n && n !== 0) return "";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / 1024 / 1024).toFixed(1) + " MB";
  }

  function setStatus(msg, kind) {
    el.status.textContent = msg || "";
    el.status.className = "status" + (kind ? " " + kind : "");
  }

  function hideViewer() {
    el.viewer.classList.add("hidden");
    el.frame.removeAttribute("src");
    el.image.removeAttribute("src");
    if (el.ofdHost) {
      el.ofdHost.classList.add("hidden");
      el.ofdHost.textContent = "";
    }
    if (el.archiveHost) {
      el.archiveHost.classList.add("hidden");
      el.archiveHost.textContent = "";
    }
  }

  /* ---------------- 不支持的格式：只给说明 ---------------- */

  function showUnsupported(data) {
    showArchive({ message: data.message || "暂不支持该格式的在线预览。", entries: [] });
    setStatus("暂不支持该格式", "error");
  }

  /* ---------------- 压缩包：列出条目 ---------------- */

  function showArchive(data) {
    el.frame.classList.add("hidden");
    el.image.classList.add("hidden");
    el.ofdHost.classList.add("hidden");
    el.archiveHost.classList.remove("hidden");
    el.archiveHost.textContent = "";

    var msg = document.createElement("p");
    msg.className = "msg";
    msg.textContent = data.message || "压缩包不支持在线预览。";
    el.archiveHost.appendChild(msg);

    var list = data.entries || [];
    if (list.length) {
      var ul = document.createElement("ul");
      list.forEach(function (item) {
        var li = document.createElement("li");
        li.textContent = item.name;
        if (!item.dir && item.size) {
          var span = document.createElement("span");
          span.className = "size";
          span.textContent = fmtSize(item.size);
          li.appendChild(span);
        }
        ul.appendChild(li);
      });
      el.archiveHost.appendChild(ul);
    }
    setStatus("压缩包：共 " + list.length + " 项（仅列出，不渲染内容）", "ok");
  }

  /* ---------------- OFD 渲染（flyfish，纯前端） ----------------
   * 按需加载 1.1MB 的渲染器，避免每次打开首页都下载。
   * 该渲染器是 Apache-2.0 的 @file-viewer/renderer-ofd，
   * 编译产物随包放在 /vendor/ofd/fv-ofd.js，无任何外部依赖。
   */
  var ofdLibPromise = null;

  function ensureOfdLib() {
    if (ofdLibPromise) return ofdLibPromise;
    ofdLibPromise = new Promise(function (resolve, reject) {
      if (window.customElements && customElements.get("flyfish-file-viewer")) {
        resolve();
        return;
      }
      var s = document.createElement("script");
      s.src = "/vendor/ofd/fv-ofd.js";
      s.onload = function () { resolve(); };
      s.onerror = function () {
        ofdLibPromise = null;
        reject(new Error("OFD 渲染器加载失败（/vendor/ofd/fv-ofd.js）"));
      };
      document.head.appendChild(s);
    });
    return ofdLibPromise;
  }

  function showOfd(url) {
    el.frame.classList.add("hidden");
    el.image.classList.add("hidden");
    el.ofdHost.classList.remove("hidden");
    el.ofdHost.textContent = "";
    setStatus("正在解析 OFD…");

    ensureOfdLib()
      .then(function () {
        var node = document.createElement("flyfish-file-viewer");
        node.setAttribute("src", url);
        node.setAttribute("theme", "light");
        node.style.cssText = "display:block;width:100%;height:100%";
        el.ofdHost.appendChild(node);

        node.addEventListener("viewer-event", function (ev) {
          var t = (ev.detail && ev.detail.type) || "";
          if (t === "load-complete") setStatus("已加载（浏览器内 OFD 渲染）", "ok");
          if (t === "load-error" || t === "error") setStatus("OFD 渲染失败，文件可能不完整或含不支持的签名", "error");
        });
      })
      .catch(function (err) {
        setStatus(err.message || "OFD 渲染器加载失败", "error");
      });
  }

  function resetPanel() {
    if (state.poll) { clearInterval(state.poll); state.poll = null; }
    el.panel.classList.add("hidden");
    el.btnConvert.classList.add("hidden");
    el.btnOcr.classList.add("hidden");
    el.btnOcrPdf.classList.add("hidden");
    el.btnPdf.classList.add("hidden");
    el.btnPreview.disabled = false;
    el.btnConvert.disabled = false;
    el.btnOcr.disabled = false;
    el.btnOcrPdf.disabled = false;
    el.btnPdf.disabled = false;
    hideViewer();
    setStatus("");
  }

  /* ---------------- 上传 ---------------- */

  /* 文件类型徽标：扩展名 + 配色 */
  var BADGES = {
    pdf: ["PDF", "b-pdf"], ofd: ["OFD", "b-ofd"],
    doc: ["DOC", "b-doc"], docx: ["DOCX", "b-doc"], rtf: ["RTF", "b-doc"],
    odt: ["ODT", "b-doc"], wps: ["WPS", "b-doc"],
    xls: ["XLS", "b-xls"], xlsx: ["XLSX", "b-xls"], csv: ["CSV", "b-xls"], et: ["ET", "b-xls"],
    ppt: ["PPT", "b-ppt"], pptx: ["PPTX", "b-ppt"], dps: ["DPS", "b-ppt"],
    zip: ["ZIP", "b-zip"], rar: ["RAR", "b-zip"], "7z": ["7Z", "b-zip"],
    tar: ["TAR", "b-zip"], gz: ["GZ", "b-zip"], tgz: ["TGZ", "b-zip"], bz2: ["BZ2", "b-zip"],
    png: ["PNG", "b-img"], jpg: ["JPG", "b-img"], jpeg: ["JPG", "b-img"],
    gif: ["GIF", "b-img"], bmp: ["BMP", "b-img"], webp: ["WEBP", "b-img"], svg: ["SVG", "b-img"]
  };

  function setBadge(name) {
    var node = document.getElementById("fileBadge");
    if (!node) return;
    var ext = (name || "").toLowerCase().split(".").pop();
    var b = BADGES[ext] || ["FILE", "b-def"];
    node.textContent = b[0];
    node.className = "badge " + b[1];
  }

  function upload(file) {
    if (!file) return;
    resetPanel();
    el.panel.classList.remove("hidden");
    el.fname.textContent = file.name;
    el.fsize.textContent = fmtSize(file.size);
    setBadge(file.name);
    setStatus("上传中…");

    var form = new FormData();
    form.append("file", file, file.name);

    var headers = {};
    if (state.sid) headers["X-Session-Id"] = state.sid;

    fetch("/api/upload", { method: "POST", body: form, headers: headers })
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) throw new Error(data.detail || "上传失败");
          return data;
        });
      })
      .then(function (data) {
        state.sid = data.session;
        state.key = data.key;
        state.info = data;
        el.btnConvert.classList.toggle("hidden", !data.convertible);
        el.btnOcr.classList.toggle("hidden", !data.ocr);
        el.btnOcrPdf.classList.toggle("hidden", !data.ocr);
        // 仅对真正能导出 PDF 的格式显示按钮（服务端判定，Excel 不在其中）
        el.btnPdf.classList.toggle("hidden", !data.pdf_export);
        setStatus("已就绪：" + data.name + "（" + fmtSize(data.size) + "）", "ok");
        preview();
      })
      .catch(function (err) {
        setStatus(err.message || "上传失败", "error");
      });
  }

  /* ---------------- 预览 ---------------- */

  function preview() {
    if (!state.sid || !state.key) return;
    setStatus("生成预览…");
    fetch("/api/preview/" + encodeURIComponent(state.sid) + "/" + encodeURIComponent(state.key))
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) throw new Error(data.detail || "预览失败");
          return data;
        });
      })
      .then(function (data) {
        el.viewer.classList.remove("hidden");
        if (data.mode === "native") {
          var ext = (state.info && state.info.name || "").toLowerCase().split(".").pop();
          if (["png", "jpg", "jpeg", "gif", "bmp", "webp", "svg"].indexOf(ext) >= 0) {
            el.frame.classList.add("hidden");
            el.image.classList.remove("hidden");
            el.image.src = data.url;
          } else {
            el.image.classList.add("hidden");
            el.frame.classList.remove("hidden");
            el.frame.src = data.url;
          }
          setStatus("已加载（浏览器原生渲染）", "ok");
        } else if (data.mode === "ofd") {
          showOfd(data.url);
        } else if (data.mode === "archive") {
          showArchive(data);
        } else if (data.mode === "unsupported") {
          showUnsupported(data);
        } else {
          el.image.classList.add("hidden");
          el.frame.classList.remove("hidden");
          el.frame.src = data.url;
          setStatus("已加载（服务端转码渲染）", "ok");
        }
      })
      .catch(function (err) {
        setStatus(err.message || "预览失败", "error");
      });
  }

  /* ---------------- 转 Word ---------------- */

  function convert() {
    if (!state.sid || !state.key) return;
    el.btnConvert.disabled = true;
    setStatus("正在转换，请稍候…");

    fetch("/api/convert/" + encodeURIComponent(state.sid) + "/" + encodeURIComponent(state.key), {
      method: "POST"
    })
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) throw new Error(data.detail || "无法开始转换");
          return data;
        });
      })
      .then(function (data) {
        pollJob(data.job);
      })
      .catch(function (err) {
        el.btnConvert.disabled = false;
        setStatus(err.message || "转换失败", "error");
      });
  }

  function pollJob(jobId, maxMinutes) {
    var limit = (maxMinutes || 10) * 60 * 1000;
    var started = Date.now();
    state.poll = setInterval(function () {
      if (Date.now() - started > limit) {
        clearInterval(state.poll); state.poll = null;
        el.btnConvert.disabled = false;
        el.btnOcr.disabled = false;
        el.btnOcrPdf.disabled = false;
        setStatus("处理超时，请重试", "error");
        return;
      }
      fetch("/api/convert/status/" + encodeURIComponent(jobId))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.status === "done") {
            clearInterval(state.poll); state.poll = null;
            el.btnConvert.disabled = false;
            el.btnOcr.disabled = false;
            el.btnOcrPdf.disabled = false;
            setStatus("", "");
            el.status.className = "status ok";
            el.status.textContent = "处理完成，点击下载（会话有效期内可重复下载）： ";
            var a = document.createElement("a");
            bindDownload(a, d.download, d.filename || "download");
            a.textContent = d.filename || "下载";
            el.status.appendChild(a);
          } else if (d.status === "failed") {
            clearInterval(state.poll); state.poll = null;
            el.btnConvert.disabled = false;
            el.btnOcr.disabled = false;
            el.btnOcrPdf.disabled = false;
            var msg = d.error || "处理失败";
            setStatus(msg, "error");
            // 扫描件：引导用户改用 OCR
            if (msg.indexOf("扫描件") >= 0 && el.btnOcr && !el.btnOcr.classList.contains("hidden")) {
              var tip = document.createElement("div");
              tip.style.marginTop = "6px";
              var b = document.createElement("a");
              b.href = "#";
              b.textContent = "→ 改用 OCR 识别文字";
              b.onclick = function (ev) { ev.preventDefault(); ocrConvert(); };
              tip.appendChild(b);
              el.status.appendChild(tip);
            }
          } else {
            var tip2 = d.status === "queued" ? "排队中…" : "正在处理，请稍候…";
            setStatus(tip2);
          }
        })
        .catch(function () { /* 单次轮询失败不中断 */ });
    }, 1200);
  }

  /* ---------------- 扫描件 OCR（两种产物） ----------------
   * 1) 提取文字 → 纯文字 Word：轻，但版式全丢
   * 2) 可搜索 PDF：原图一像素不动，只叠加不可见文字层，外观 100% 保留
   */

  function startOcr(path, label, btn) {
    if (!state.sid || !state.key) return;
    btn.disabled = true;
    el.btnConvert.disabled = true;
    el.btnOcr.disabled = true;
    el.btnOcrPdf.disabled = true;
    setStatus("正在" + label + "（每页数秒，页数多会比较久）…");

    fetch(path, { method: "POST" })
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) throw new Error(data.detail || ("无法开始" + label));
          return data;
        });
      })
      .then(function (data) { pollJob(data.job, 40); })
      .catch(function (err) {
        el.btnOcr.disabled = false;
        el.btnOcrPdf.disabled = false;
        el.btnConvert.disabled = false;
        setStatus(err.message || "OCR 启动失败", "error");
      });
  }

  function ocrConvert() {
    startOcr(
      "/api/ocr/" + encodeURIComponent(state.sid) + "/" + encodeURIComponent(state.key),
      "OCR 识别文字",
      el.btnOcr
    );
  }

  function ocrPdf() {
    startOcr(
      "/api/ocrpdf/" + encodeURIComponent(state.sid) + "/" + encodeURIComponent(state.key),
      "生成可搜索 PDF",
      el.btnOcrPdf
    );
  }

  /* ---------------- 转 PDF ---------------- */

  function toPdf() {
    if (!state.sid || !state.key) return;
    el.btnPdf.disabled = true;
    setStatus("正在生成 PDF（首次需转码，请稍候）…");

    var url = "/api/pdf/" + encodeURIComponent(state.sid) + "/" + encodeURIComponent(state.key);

    fetch(url)
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (d) {
            throw new Error(d.detail || ("生成失败（" + r.status + "）"));
          });
        }
        return r.blob();
      })
      .then(function (blob) {
        var base = (state.info && state.info.name || "file").replace(/\.[^.]+$/, "");
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = base + ".pdf";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 10000);
        el.btnPdf.disabled = false;
        setStatus("PDF 已生成并开始下载", "ok");
      })
      .catch(function (err) {
        el.btnPdf.disabled = false;
        setStatus(err.message || "生成 PDF 失败", "error");
      });
  }

  /* ---------------- 清理 ---------------- */

  function closeSession(useBeacon) {
    if (!state.sid || state.closed) { state.closed = true; return; }
    var sid = state.sid;
    state.closed = true;

    if (useBeacon && navigator.sendBeacon) {
      var blob = new Blob([JSON.stringify({ session: sid })], { type: "application/json" });
      navigator.sendBeacon("/api/session/close", blob);
    } else {
      fetch("/api/session/" + encodeURIComponent(sid) + "/close", { method: "POST" }).catch(function () {});
    }
    state.sid = null; state.key = null; state.info = null;
  }

  /* 下载：用 fetch 取 blob 再存盘，**不能让浏览器做页面跳转**。
   *
   * 直接点 <a href="/api/download/..."> 时，浏览器把它当导航处理，
   * 页面触发 pagehide -> sendBeacon 通知服务端清理会话 -> 令牌被删，
   * 随后到达的下载请求就 410「下载链接不存在」。
   * 保留 href 是为了支持右键另存 / 拖到新窗口。
   */
  function saveBlob(blob, filename) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 10000);
  }

  function bindDownload(link, url, filename) {
    if (!url) return;
    link.href = url;
    link.addEventListener("click", function (e) {
      e.preventDefault();
      setStatus("正在下载…", "");
      fetch(url)
        .then(function (r) {
          if (!r.ok) {
            return r.json().then(function (d) {
              throw new Error(d.detail || ("下载失败（" + r.status + "）"));
            });
          }
          return r.blob();
        })
        .then(function (blob) {
          saveBlob(blob, filename);
          setStatus("已开始下载：" + filename, "ok");
        })
        .catch(function (err) {
          setStatus(err.message || "下载失败，会话可能已结束", "error");
        });
    });
  }

  /* ---------------- 事件绑定 ---------------- */

  /* 360 / QQ 等双核浏览器会把「已选中的文件」丢掉，导致 change 不触发，
     表现为点击选择文件后毫无反应。检测到就提示换浏览器。 */
  (function warnDualCoreBrowser() {
    var ua = navigator.userAgent || "";
    if (!/360SE|360EE|QIHU|MetaSr|QQBrowser/i.test(ua)) return;
    var box = document.getElementById("browserWarn");
    if (box) box.classList.remove("hidden");
  })();

  /* 点击由 <label for="picker"> 原生触发，这里不再调用 picker.click()，
     否则在部分内核下会弹出两次文件选择框。
     键盘操作仍需手动触发（label 不响应回车）。 */
  el.drop.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.picker.click(); }
  });
  el.picker.addEventListener("change", function () {
    if (el.picker.files && el.picker.files[0]) upload(el.picker.files[0]);
    el.picker.value = "";
  });

  ["dragenter", "dragover"].forEach(function (ev) {
    el.drop.addEventListener(ev, function (e) {
      e.preventDefault(); e.stopPropagation();
      el.drop.classList.add("over");
    });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    el.drop.addEventListener(ev, function (e) {
      e.preventDefault(); e.stopPropagation();
      el.drop.classList.remove("over");
    });
  });
  el.drop.addEventListener("drop", function (e) {
    var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) upload(f);
  });

  el.btnPreview.addEventListener("click", preview);
  el.btnConvert.addEventListener("click", convert);
  el.btnOcr.addEventListener("click", ocrConvert);
  el.btnOcrPdf.addEventListener("click", ocrPdf);
  el.btnPdf.addEventListener("click", toPdf);
  el.btnClose.addEventListener("click", function () {
    closeSession(false);
    resetPanel();
    el.drop.classList.remove("hidden");
    setStatus("已清理服务器端临时文件", "ok");
  });

  window.addEventListener("pagehide", function () { closeSession(true); });
  window.addEventListener("beforeunload", function () { closeSession(true); });
})();
