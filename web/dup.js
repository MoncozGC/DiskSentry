/* DiskSentry 重复文件清理页逻辑。 */
(function () {
  'use strict';
  var root = document.getElementById('dm-root');
  if (!root) return;
  var $ = function (s) { return root.querySelector(s); };

  var FOLDER_SVG = '<svg class="lucide" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/></svg>';

  function api(path, opts) {
    return fetch(path, opts).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
        return j;
      });
    });
  }
  function fmtBytes(b) {
    var a = Math.abs(b);
    if (a >= 1024 * 1024 * 1024) return (a / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
    if (a >= 1024 * 1024) return (a / (1024 * 1024)).toFixed(1) + ' MB';
    if (a >= 1024) return (a / 1024).toFixed(0) + ' KB';
    return a + ' B';
  }
  function pad2(n) { return String(n).padStart(2, '0'); }
  function fmtTime(ts) {
    var d = new Date(ts * 1000);
    return pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) + ' ' +
      pad2(d.getHours()) + ':' + pad2(d.getMinutes());
  }
  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ------------------------------------------------------------ 初始化
  api('/api/state').then(function (st) {
    var list = $('#dup-roots');
    list.innerHTML = (st.available || []).map(function (d) {
      return '<option value="' + esc(d.root) + '">' + esc(d.root) + '</option>';
    }).join('');
    var input = $('#dup-root');
    if (!input.value && st.config && st.config.disks && st.config.disks.length) {
      input.value = st.config.disks[0];
    }
  }).catch(function () {});

  var pollTimer = null;

  $('#dup-scan-btn').addEventListener('click', function () {
    var path = $('#dup-root').value.trim();
    if (!path) { alert('请先输入或选择扫描目录'); return; }
    api('/api/dup/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ root: path })
    }).then(function () {
      $('#dup-progress').hidden = false;
      setProgress('正在遍历目录…');
      $('#dup-scan-btn').disabled = true;
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(pollStatus, 800);
    }).catch(function (e) { alert('启动扫描失败：' + e.message); });
  });

  function setProgress(text) { $('#dup-progress-text').textContent = text; }

  function pollStatus() {
    api('/api/dup/status').then(function (st) {
      if (st.error) {
        stopPoll();
        setProgress('扫描失败：' + st.error);
        return;
      }
      if (st.running) {
        var p = st.progress || {};
        if (p.phase === 'hash' && p.total) {
          setProgress('正在比对文件内容（MD5）· ' + p.done + ' / ' + p.total);
        } else {
          setProgress('正在遍历目录…');
        }
        return;
      }
      if (st.has_result) {
        stopPoll();
        setProgress('扫描完成');
        loadResult();
      }
    }).catch(function () {});
  }

  function stopPoll() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
    $('#dup-scan-btn').disabled = false;
  }

  function loadResult() {
    api('/api/dup/result').then(render).catch(function (e) {
      $('#dup-groups').innerHTML = '<div class="dup-empty">' + esc(e.message) + '</div>';
    });
  }

  // ------------------------------------------------------------ 渲染
  function rowHtml(gid, f, checked, keeper, sizeText) {
    return '<div class="dup-row" data-path="' + esc(f.path) + '">' +
      '<input type="checkbox" class="dup-check" data-path="' + esc(f.path) + '"' +
      (checked ? ' checked' : '') + ' aria-label="选择 ' + esc(f.path) + '">' +
      (keeper ? '<span class="dup-keep">保留</span>' : '') +
      '<span class="dup-path dm-mono" title="' + esc(f.path) + '">' + esc(f.path) + '</span>' +
      '<span class="dup-time dm-num dm-muted">' + fmtTime(f.mtime) + '</span>' +
      '<span class="dup-size dm-num dm-muted">' + sizeText + '</span>' +
      '<button type="button" class="dm-btn dm-btn-icon dup-reveal" data-path="' + esc(f.path) +
      '" title="在资源管理器中显示" aria-label="在资源管理器中显示 ' + esc(f.path) + '">' + FOLDER_SVG + '</button>' +
    '</div>';
  }

  function render(result) {
    var dupEl = $('#dup-groups'), simEl = $('#sim-groups');

    if (!result.duplicate_groups.length && !result.similar_groups.length) {
      dupEl.innerHTML = '<div class="dup-empty">✅ 没有发现重复文件（共扫描 ' +
        result.file_count + ' 个文件）</div>';
      simEl.innerHTML = '';
      $('#dup-summary').hidden = true;
      return;
    }

    dupEl.innerHTML = result.duplicate_groups.map(function (g, gi) {
      var sizeText = fmtBytes(g.size);
      var waste = g.size * (g.files.length - 1);
      var keeper = g.keep || 0;
      var rows = g.files.map(function (f, i) {
        return rowHtml('d' + gi, f, i !== keeper, i === keeper, sizeText);
      }).join('');
      return '<div class="dup-group" data-gid="d' + gi + '">' +
        '<div class="dup-group-head">' +
          '<label class="dup-group-check"><input type="checkbox" class="dup-gcheck"> 全选可删</label>' +
          '<span class="dup-group-title dm-num">' + g.files.length + ' 个文件 · 每个 ' + sizeText +
          ' · 可释放 <b class="dup-waste">' + fmtBytes(waste) + '</b></span>' +
        '</div>' + rows + '</div>';
    }).join('') || '<div class="dup-empty">没有 MD5 完全一致的文件</div>';

    simEl.innerHTML = result.similar_groups.map(function (g, si) {
      var keeper = g.keep || 0;
      var rows = g.files.map(function (f, i) {
        return rowHtml('s' + si, f, i !== keeper, i === keeper, fmtBytes(f.size));
      }).join('');
      return '<div class="dup-group" data-gid="s' + si + '">' +
        '<div class="dup-group-head">' +
          '<label class="dup-group-check"><input type="checkbox" class="dup-gcheck"> 全选可删</label>' +
          '<span class="dup-group-title">“' + esc(g.name) + '” · ' + g.files.length +
          ' 个版本 · 建议保留无 (1)/副本 后缀的文件（标有"保留"）</span>' +
        '</div>' + rows + '</div>';
    }).join('') || '<div class="dup-empty">没有名字相近但内容不同的版本文件</div>';

    updateSummary();
  }

  function updateSummary() {
    var checked = root.querySelectorAll('.dup-check:checked');
    var total = 0, count = 0;
    checked.forEach(function (cb) {
      var row = cb.closest('.dup-row');
      var sizeText = row.querySelector('.dup-size').textContent;
      total += parseSize(sizeText);
      count++;
    });
    var dupGroups = root.querySelectorAll('#dup-groups .dup-group').length;
    var simGroups = root.querySelectorAll('#sim-groups .dup-group').length;
    if (!dupGroups && !simGroups) { $('#dup-summary').hidden = true; return; }
    $('#dup-summary').hidden = false;
    $('#dup-summary-text').innerHTML =
      '完全重复 <b>' + dupGroups + '</b> 组 · 同名版本 <b>' + simGroups + '</b> 组 · ' +
      '已勾选 <b class="dm-num">' + count + '</b> 个文件，约 <b class="dm-num">' +
      fmtBytes(total) + '</b>';
    var btn = $('#dup-delete-btn');
    btn.disabled = count === 0;
    btn.textContent = count ? '删除所选 ' + count + ' 个（入回收站）' : '删除所选';
  }

  function parseSize(text) {
    var m = text.match(/([\d.]+)\s*(GB|MB|KB|B)/);
    if (!m) return 0;
    var v = parseFloat(m[1]);
    return Math.round(v * { GB: 1024 ** 3, MB: 1024 ** 2, KB: 1024, B: 1 }[m[2]]);
  }

  // ------------------------------------------------------------ 删除记录
  function loadHistory() {
    api('/api/deletions?limit=200').then(renderHistory).catch(function () {});
  }

  function renderHistory(d) {
    var sub = $('#del-hist-sub'), el = $('#del-hist');
    if (!d.total) {
      sub.textContent = '尚未删除任何文件';
      el.innerHTML = '<div class="dup-empty">暂无记录</div>';
      return;
    }
    sub.textContent = '共删除 ' + d.total + ' 个文件 · 清理 ' + fmtBytes(d.total_size) +
      ' · 均可从回收站恢复';
    el.innerHTML = d.deletions.map(function (r) {
      return '<div class="dup-row">' +
        '<span class="dup-time dm-num dm-muted">' + fmtTime(r.ts) + '</span>' +
        '<span class="dup-size dm-num dm-muted">' +
          (r.size != null ? fmtBytes(r.size) : '—') + '</span>' +
        '<span class="dup-path dm-mono" title="' + esc(r.path) + '">' + esc(r.path) + '</span>' +
      '</div>';
    }).join('');
  }

  // ------------------------------------------------------------ 交互（事件委托）
  root.addEventListener('change', function (e) {
    if (e.target.classList.contains('dup-check')) {
      updateSummary();
    } else if (e.target.classList.contains('dup-gcheck')) {
      // 组头：全选/取消 该组内所有"可删项"（保留项不自动勾选）
      var group = e.target.closest('.dup-group');
      group.querySelectorAll('.dup-row').forEach(function (row) {
        var isKeeper = !!row.querySelector('.dup-keep');
        if (!isKeeper) row.querySelector('.dup-check').checked = e.target.checked;
      });
      updateSummary();
    }
  });

  root.addEventListener('click', function (e) {
    var reveal = e.target.closest('.dup-reveal');
    if (reveal) {
      api('/api/dup/reveal', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: reveal.dataset.path })
      }).catch(function (err) { alert('打开失败：' + err.message); });
      return;
    }
  });

  $('#dup-delete-btn').addEventListener('click', function () {
    var paths = Array.prototype.map.call(
      root.querySelectorAll('.dup-check:checked'), function (cb) {
        return cb.dataset.path;
      });
    if (!paths.length) return;
    if (!window.confirm('确认删除 ' + paths.length + ' 个文件到回收站？\n（可从回收站恢复）')) {
      return;
    }
    // 分批提交：避免请求体过大，也避免一次删除数千文件时长时间无响应
    var BATCH = 300;
    var batches = [];
    for (var i = 0; i < paths.length; i += BATCH) batches.push(paths.slice(i, i + BATCH));

    var btn = $('#dup-delete-btn');
    btn.disabled = true;
    var deleted = [], failed = [];

    (function next(bi) {
      if (bi >= batches.length) return finish();
      btn.textContent = '正在删除 ' + Math.min((bi + 1) * BATCH, paths.length) +
        ' / ' + paths.length + ' …';
      api('/api/dup/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths: batches[bi] })
      }).then(function (res) {
        deleted = deleted.concat(res.deleted || []);
        failed = failed.concat(res.failed || []);
        next(bi + 1);
      }).catch(function (err) {
        failed.push({ path: '(第 ' + (bi + 1) + ' 批，' + err.message + ')' });
        next(bi + 1);  // 单批失败不阻断后续批次
      });
    })(0);

    function finish() {
      // 移除已删除的行；组内剩 <2 个文件则整组移除
      deleted.forEach(function (p) {
        var row = root.querySelector('.dup-row[data-path="' + CSS.escape(p) + '"]');
        if (row) {
          var group = row.closest('.dup-group');
          row.remove();
          if (group && group.querySelectorAll('.dup-row').length < 2) group.remove();
        }
      });
      var msg = $('#dup-result-msg');
      msg.hidden = false;
      if (failed.length) {
        msg.className = 'dup-result-msg dup-msg-error';
        msg.textContent = '已删除 ' + deleted.length + ' 个，失败 ' + failed.length +
          ' 项：' + failed.slice(0, 3).map(function (f) { return f.path; }).join('；');
      } else {
        msg.className = 'dup-result-msg';
        msg.textContent = '已删除 ' + deleted.length + ' 个文件（在回收站中，可恢复）';
      }
      setTimeout(function () { msg.hidden = true; }, 8000);
      updateSummary();
      loadHistory();
    }
  });

  loadHistory();

  updateSummary();
})();
