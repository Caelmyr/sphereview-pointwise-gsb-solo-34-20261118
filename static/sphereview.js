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
let maskDrawing = false;
let maskStart = null;
let maskDraft = null;

const clamp = value => Math.min(1, Math.max(0, value));

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
    $('mask-tools').hidden = mode !== 'view';
    if (mode !== 'view') disarmMask();
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
        $('probe-readout').textContent = '点击方向图转到指定位置并读取原图像素。';
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
        $('probe-readout').textContent = '点击方向图转到指定位置并读取原图像素。';
    }
    $('marker').style.left = (project.view.yaw + 180) / 3.6 + '%';
    $('marker').style.top = (90 - project.view.pitch) / 1.8 + '%';
    $('marker-position').textContent = project.view.yaw.toFixed(1) + '° / ' + project.view.pitch.toFixed(1) + '°';
    populateBookmarks();
    showMode();
    syncMaskUI();
}

function syncMaskUI() {
    const has = Boolean(current && current.mask);
    $('mask-clear').disabled = !has;
    $('mask-status').textContent = has
        ? '已保存一块纯色遮挡，固定在场景同一处；换方向或画幅后依然跟随。可重新框选替换，或清除。'
        : '在透视取景画面上拖出一个矩形，遮挡会固定在场景同一处，原始全景不会改动。';
}

function positionMaskOverlay() {
    const hero = $('hero').getBoundingClientRect();
    const stage = $('primary-image').getBoundingClientRect();
    const overlay = $('mask-overlay');
    overlay.style.left = (hero.left - stage.left) + 'px';
    overlay.style.top = (hero.top - stage.top) + 'px';
    overlay.style.width = hero.width + 'px';
    overlay.style.height = hero.height + 'px';
}

function disarmMask() {
    maskDrawing = false;
    maskStart = null;
    maskDraft = null;
    $('mask-overlay').hidden = true;
    $('mask-overlay').replaceChildren();
    $('mask-draw').removeAttribute('aria-pressed');
    $('mask-draw').textContent = '框选遮挡区域';
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

$('direction-map').addEventListener('click', run(async event => {
    const box = $('direction-map').getBoundingClientRect();
    const yaw = +(((event.clientX - box.left) / box.width) * 360 - 180).toFixed(2);
    const pitch = Math.min(89.5, Math.max(-89.5, +(90 - ((event.clientY - box.top) / box.height) * 180).toFixed(2)));
    mode = 'view';
    await edit('view', {view: {...current.view, yaw, pitch}});
    const search = new URLSearchParams({yaw, pitch});
    const probe = await json(route(current.id, 'probe') + '?' + search);
    $('probe-readout').textContent = '原图像素 (' + probe.column + ', ' + probe.row +
        ')  ·  RGBA ' + probe.rgba.join(', ');
}));

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

$('mask-draw').addEventListener('click', run(async () => {
    if (mode !== 'view' || !current) return;
    if (maskDrawing) {
        disarmMask();
        return;
    }
    maskDrawing = true;
    positionMaskOverlay();
    $('mask-overlay').hidden = false;
    $('mask-draw').setAttribute('aria-pressed', 'true');
    $('mask-draw').textContent = '取消框选';
    $('mask-status').textContent = '在画面上按住并拖出矩形；按 Esc 可取消。遮挡按当前取景固定到场景位置。';
}));

$('mask-clear').addEventListener('click', run(() => edit('clear-mask', {})));

$('mask-overlay').addEventListener('pointerdown', event => {
    if (!maskDrawing) return;
    event.preventDefault();
    const box = $('mask-overlay').getBoundingClientRect();
    maskStart = {
        x: clamp((event.clientX - box.left) / box.width),
        y: clamp((event.clientY - box.top) / box.height)
    };
    maskDraft = document.createElement('div');
    maskDraft.className = 'draft';
    $('mask-overlay').replaceChildren(maskDraft);
    $('mask-overlay').setPointerCapture(event.pointerId);
});

$('mask-overlay').addEventListener('pointermove', event => {
    if (!maskDrawing || !maskStart || !maskDraft) return;
    const box = $('mask-overlay').getBoundingClientRect();
    const x = clamp((event.clientX - box.left) / box.width);
    const y = clamp((event.clientY - box.top) / box.height);
    const left = Math.min(maskStart.x, x) * box.width;
    const top = Math.min(maskStart.y, y) * box.height;
    maskDraft.style.left = left + 'px';
    maskDraft.style.top = top + 'px';
    maskDraft.style.width = Math.abs(x - maskStart.x) * box.width + 'px';
    maskDraft.style.height = Math.abs(y - maskStart.y) * box.height + 'px';
});

$('mask-overlay').addEventListener('pointerup', run(async event => {
    if (!maskDrawing || !maskStart) return;
    const box = $('mask-overlay').getBoundingClientRect();
    const x = clamp((event.clientX - box.left) / box.width);
    const y = clamp((event.clientY - box.top) / box.height);
    const left = Math.min(maskStart.x, x);
    const right = Math.max(maskStart.x, x);
    const top = Math.min(maskStart.y, y);
    const bottom = Math.max(maskStart.y, y);
    maskStart = null;
    if (right - left < 0.01 || bottom - top < 0.01) {
        $('mask-status').textContent = '矩形太小，请重新框选。';
        disarmMask();
        return;
    }
    try {
        await edit('mask', {box: {left, right, top, bottom}, color: $('mask-color').value});
        $('status').textContent = '遮挡已保存，下载的取景 PNG 会包含同一块纯色遮挡。';
    } finally {
        disarmMask();
    }
}));

document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && maskDrawing) disarmMask();
});
window.addEventListener('resize', () => {
    if (maskDrawing) positionMaskOverlay();
});

run(projectList)();
