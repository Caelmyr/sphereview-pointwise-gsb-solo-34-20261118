'use strict';

const $ = id => document.getElementById(id);
const modes = {
    view: ['透视取景', 'view.png'],
    leveled: ['整站校准经纬图', 'leveled.png'],
    cube: ['六张定向方形贴图', 'cube.zip'],
    planet: ['地面居中的小行星投影', 'planet.png'],
    source: ['未经调整的经纬原图', 'source.png']
};
const faces = {
    left: '左', front: '前', right: '右', back: '后', top: '顶', bottom: '底'
};
let current = null;
let mode = 'view';
let busy = false;
let navToken = '';
let drag = null;

const PROBE_HINT = '点击方向图转到指定位置并读取原图像素；按住拖拽可圈选一块分析主色。';

function route(key, product = '') {
    return '/sphere/projects/' + key + (product ? '/' + product : '');
}

async function json(url, options) {
    const response = await fetch(url, options);
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || '请求失败');
    return body;
}

function post(url, content) {
    return json(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(content)});
}

function run(action) {
    return async event => {
        if (event) event.preventDefault();
        if (busy) return;
        busy = true;
        $('status').textContent = '';
        try {
            await action(event);
        } catch (error) {
            $('status').textContent = error.message;
        } finally {
            busy = false;
        }
    };
}

function inputValues(form, keys) {
    return Object.fromEntries(keys.map(key => [key, form.elements[key].value]));
}

function setValues(form, values) {
    for (const [key, value] of Object.entries(values)) {
        form.elements[key].value = value;
    }
}

function showMode() {
    if (!current) return;
    $('planet-settings').hidden = mode !== 'planet';
    for (const button of $('modes').querySelectorAll('button')) {
        button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
    }
    const [description, filename] = modes[mode];
    $('mode-description').textContent = description;
    $('output').textContent = mode === 'cube' ? '下载六面 ZIP ↓' : '下载 PNG ↓';
    $('output').href = route(current.id, filename) + '?download=1';
    $('hero').hidden = mode === 'cube';
    $('cube-grid').hidden = mode !== 'cube';
    $('primary-image').classList.toggle('six-faces', mode === 'cube');
    if (mode === 'cube') {
        $('cube-grid').replaceChildren(...Object.entries(faces).map(([key, label]) => {
            const figure = document.createElement('figure');
            const image = document.createElement('img');
            image.src = route(current.id, 'cube/' + key + '.png') + '?r=' + current.revision;
            image.alt = label + '面';
            const caption = document.createElement('figcaption');
            caption.textContent = label;
            figure.append(image, caption);
            return figure;
        }));
    } else {
        $('hero').src = route(current.id, filename) + '?r=' + current.revision;
    }
}

async function projectList() {
    const projects = await json('/sphere/projects');
    $('projects').replaceChildren(...projects.map(project => {
        const row = document.createElement('li');
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = project.name;
        button.setAttribute('aria-current', current?.id === project.id ? 'true' : 'false');
        button.addEventListener('click', run(async () => {
            mode = 'view';
            setProject(await json(route(project.id)));
            await projectList();
        }));
        row.append(button);
        return row;
    }));
}

function setProject(project) {
    if (current?.id !== project.id) {
        $('probe-readout').textContent = PROBE_HINT;
        $('seam-result').replaceChildren();
    }
    current = project;
    $('workspace').hidden = false;
    $('empty').hidden = true;
    $('project-name').textContent = project.name;
    $('source-meta').textContent = [
        project.source.width + ' × ' + project.source.height + ' px',
        project.synthetic ? '合成演示' : '导入照片',
        '修订 ' + project.revision
    ].join('  ·  ');
    $('project-zip').href = route(project.id, 'project.zip');
    setValues($('view-form'), project.view);
    setValues($('pose-form'), project.pose);
    setValues($('planet-form'), project.planet);
    setValues($('exposure-form'), {exposure: project.exposure});
    $('new-name').value = project.name;
    const token = JSON.stringify([project.id, project.pose, project.exposure]);
    if (token !== navToken) {
        $('navigation-image').src = route(project.id, 'leveled.png') + '?r=' + project.revision;
        navToken = token;
        $('probe-readout').textContent = PROBE_HINT;
        clearSelection();
    }
    $('marker').style.left = (project.view.yaw + 180) / 3.6 + '%';
    $('marker').style.top = (90 - project.view.pitch) / 1.8 + '%';
    $('marker-position').textContent = project.view.yaw.toFixed(1) + '° / ' + project.view.pitch.toFixed(1) + '°';
    populateBookmarks();
    showMode();
}

async function edit(action, values) {
    const data = await post(route(current.id, 'edit'), {revision: current.revision, action, ...values});
    setProject(data);
}

function populateBookmarks() {
    const cards = current.bookmarks.map(item => {
        const card = document.createElement('article');
        const image = document.createElement('img');
        image.alt = item.name + '的取景';
        image.loading = 'lazy';
        image.src = route(current.id, 'bookmark/' + item.id + '.png') + '?r=' + current.revision;
        const info = document.createElement('div');
        const title = document.createElement('h4');
        title.textContent = item.name;
        const recall = document.createElement('button');
        recall.textContent = '切换取景';
        recall.addEventListener('click', run(async () => {
            mode = 'view';
            await edit('recall', {bookmark: item.id});
        }));
        const remove = document.createElement('button');
        remove.textContent = '移除';
        remove.addEventListener('click', run(() => edit('delete-bookmark', {bookmark: item.id})));
        info.append(title, recall, remove);
        card.append(image, info);
        return card;
    });
    $('bookmark-list').replaceChildren(...cards);
}

function bindUpload(open, input, path) {
    $(open).addEventListener('click', () => $(input).click());
    $(input).addEventListener('change', run(async event => {
        const file = event.target.files[0];
        event.target.value = '';
        if (!file) return;
        const body = new FormData();
        body.append('file', file);
        mode = 'view';
        setProject(await json(path, {method: 'POST', body}));
        await projectList();
    }));
}

bindUpload('upload-open', 'upload', '/sphere/projects');
bindUpload('restore-open', 'restore', '/sphere/restore');
$('demo').addEventListener('click', run(async () => {
    mode = 'view';
    setProject(await post('/sphere/demo', {}));
    await projectList();
}));

for (const button of $('modes').querySelectorAll('button')) {
    button.addEventListener('click', () => {
        mode = button.dataset.mode;
        showMode();
    });
}

$('view-form').addEventListener('submit', run(event => {
    mode = 'view';
    return edit('view', {view: inputValues(event.target, ['yaw', 'pitch', 'roll', 'fov', 'aspect'])});
}));
$('pose-form').addEventListener('submit', run(event => {
    mode = 'leveled';
    return edit('pose', {pose: inputValues(event.target, ['yaw', 'pitch', 'roll'])});
}));
$('planet-form').addEventListener('submit', run(event => edit('planet', {
    planet: inputValues(event.target, ['spin', 'radius'])
})));
$('exposure-form').addEventListener('submit', run(event => edit('exposure', inputValues(event.target, ['exposure']))));
$('bookmark-form').addEventListener('submit', run(async () => {
    await edit('bookmark', {name: $('bookmark-name').value});
    $('bookmark-name').value = '';
}));
$('rename-form').addEventListener('submit', run(async () => {
    await edit('name', {name: $('new-name').value});
    await projectList();
}));

async function probeAt(event) {
    const box = $('direction-map').getBoundingClientRect();
    const yaw = +(((event.clientX - box.left) / box.width) * 360 - 180).toFixed(2);
    const pitch = Math.min(89.5, Math.max(-89.5, +(90 - ((event.clientY - box.top) / box.height) * 180).toFixed(2)));
    mode = 'view';
    await edit('view', {view: {...current.view, yaw, pitch}});
    const search = new URLSearchParams({yaw, pitch});
    const probe = await json(route(current.id, 'probe') + '?' + search);
    $('probe-readout').textContent = '原图像素 (' + probe.column + ', ' + probe.row +
        ')  ·  RGBA ' + probe.rgba.join(', ');
}

function regionFromDrag(state, event) {
    const rect = $('direction-map').getBoundingClientRect();
    const fx = x => (x - rect.left) / rect.width;
    const fy = y => Math.min(1, Math.max(0, (y - rect.top) / rect.height));
    const wrap = x => ((x + 180) % 360 + 360) % 360 - 180;
    let lo = Math.min(fx(state.x0), fx(event.clientX)) * 360 - 180;
    let hi = Math.max(fx(state.x0), fx(event.clientX)) * 360 - 180;
    if (hi - lo < 0.5) [lo, hi] = [(lo + hi) / 2 - 0.25, (lo + hi) / 2 + 0.25];
    let yaw0 = -180, yaw1 = 180;
    if (hi - lo < 360) [yaw0, yaw1] = [wrap(lo), wrap(hi)];
    let top = Math.min(fy(state.y0), fy(event.clientY));
    let bottom = Math.max(fy(state.y0), fy(event.clientY));
    if (bottom - top < 0.5 / 180) [top, bottom] = [(top + bottom) / 2 - 0.25 / 180, (top + bottom) / 2 + 0.25 / 180];
    top = Math.max(0, top);
    bottom = Math.min(1, bottom);
    return {
        yaw0: +yaw0.toFixed(2), yaw1: +yaw1.toFixed(2),
        pitch0: +(90 - bottom * 180).toFixed(2), pitch1: +(90 - top * 180).toFixed(2)
    };
}

function drawSelection(region) {
    const top = (90 - region.pitch1) / 1.8;
    const height = (region.pitch1 - region.pitch0) / 1.8;
    const left = (region.yaw0 + 180) / 3.6;
    const right = (region.yaw1 + 180) / 3.6;
    const spans = region.yaw0 <= region.yaw1
        ? [[left, right - left]]
        : [[left, 100 - left], [0, right]];
    $('selection-layer').replaceChildren(...spans.map(([x, width]) => {
        const box = document.createElement('div');
        box.className = 'selection-box';
        Object.assign(box.style, {left: x + '%', width: width + '%', top: top + '%', height: height + '%'});
        return box;
    }));
}

function clearSelection() {
    $('selection-layer').replaceChildren();
    $('palette-result').replaceChildren();
}

function hexOf(rgb) {
    return '#' + rgb.map(value => value.toString(16).padStart(2, '0')).join('');
}

function renderPalette(result) {
    const target = $('palette-result');
    if (!result.colors.length) {
        target.textContent = '选区内没有可见像素。';
        return;
    }
    const width = ((result.region.yaw1 - result.region.yaw0 + 360) % 360) || 360;
    const height = result.region.pitch1 - result.region.pitch0;
    const headline = document.createElement('p');
    headline.textContent = `选区 ${width.toFixed(1)}° × ${height.toFixed(1)}° · ${result.pixels} 像素 · 主色按可见立体角份额`;
    const strip = document.createElement('div');
    strip.className = 'palette-strip';
    const rows = document.createElement('div');
    rows.className = 'palette-rows';
    for (const color of result.colors) {
        const hex = hexOf(color.rgb);
        const segment = document.createElement('span');
        segment.style.background = hex;
        segment.style.width = (color.share * 100) + '%';
        strip.append(segment);
        const row = document.createElement('div');
        const chip = document.createElement('span');
        chip.className = 'chip';
        chip.style.background = hex;
        row.append(chip, document.createTextNode(`${hex} · ${(color.share * 100).toFixed(1)}%`));
        rows.append(row);
    }
    if (result.remainder > 0) {
        const segment = document.createElement('span');
        segment.className = 'remainder';
        segment.style.width = (result.remainder * 100) + '%';
        strip.append(segment);
        const row = document.createElement('div');
        row.textContent = `其余零散颜色 · ${(result.remainder * 100).toFixed(1)}%`;
        rows.append(row);
    }
    const clear = document.createElement('button');
    clear.type = 'button';
    clear.textContent = '清除选区';
    clear.addEventListener('click', clearSelection);
    target.replaceChildren(headline, strip, rows, clear);
}

$('direction-map').addEventListener('pointerdown', event => {
    if (event.button !== 0 || busy || !current) return;
    event.preventDefault();
    drag = {id: event.pointerId, x0: event.clientX, y0: event.clientY, moved: false};
    $('direction-map').setPointerCapture(event.pointerId);
});

$('direction-map').addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.id) return;
    if (!drag.moved && Math.hypot(event.clientX - drag.x0, event.clientY - drag.y0) < 4) return;
    drag.moved = true;
    drawSelection(regionFromDrag(drag, event));
});

$('direction-map').addEventListener('pointerup', event => {
    if (!drag || event.pointerId !== drag.id) return;
    const state = drag;
    drag = null;
    run(async () => {
        if (!state.moved) {
            await probeAt(event);
            return;
        }
        const region = regionFromDrag(state, event);
        drawSelection(region);
        renderPalette(await post(route(current.id, 'palette'), region));
    })();
});

$('direction-map').addEventListener('pointercancel', () => {
    drag = null;
});

$('seam').addEventListener('click', run(async () => {
    const report = await json(route(current.id, 'seam'));
    const headline = document.createElement('p');
    const differences = report.mean_rgb_difference == null ? '无有效对照' : report.mean_rgb_difference;
    headline.textContent = `有效行 ${report.valid_rows} / ${report.rows}，平均差 ${differences}`;
    const table = document.createElement('table');
    table.setAttribute('aria-label', '按行段的接缝差');
    for (const band of report.bands) {
        const row = table.insertRow();
        row.insertCell().textContent = `${band.row_start}–${band.row_stop - 1} 行`;
        row.insertCell().textContent = band.mean_rgb_difference ?? '—';
    }
    $('seam-result').replaceChildren(headline, table);
}));

run(projectList)();
