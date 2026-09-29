const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const source = readFileSync(path.join(__dirname, "../static/js/visitor-map.js"), "utf8");

// Minimal DOM/event boundary: no network, provider code, or invented visit data.
function fixture() {
    const nodes = {};
    const scripts = [];
    const images = [];
    const timers = new Map();
    let mutation;
    let map;
    function node() {
        return {
            dataset: {}, hidden: true, textContent: "", listeners: {},
            addEventListener(type, callback) { this.listeners[type] = callback; },
            setAttribute(name, value) { this[name] = value; },
            emit(type) { this.listeners[type]?.(); }
        };
    }
    const section = nodes["visitor-map"] = node();
    const status = nodes["visitor-map-status"] = node();
    const host = nodes["visitor-map-widget"] = node();
    host.dataset.scriptSrc = "https://mapmyvisitors.com/map.js?d=test-only";
    host.querySelector = () => map;
    host.appendChild = script => { scripts.push(script); nodes[script.id] = script; };
    const context = vm.createContext({
        document: { getElementById: id => nodes[id], createElement: node },
        window: {
            setTimeout(callback, delay) { timers.set(1, { callback, delay }); return 1; },
            clearTimeout(id) { timers.delete(id); },
            getComputedStyle: () => ({ backgroundImage: 'url("https://example.invalid/background.png")' })
        },
        MutationObserver: class {
            constructor(callback) { mutation = callback; }
            observe() {}
            disconnect() { mutation = null; }
        },
        Image: function () { const image = node(); images.push(image); return image; }
    });
    return {
        section, status, host, scripts, images, timers,
        start() { vm.runInContext(source, context); },
        render() {
            // Provider maps contain an SVG even with no visits or SVG land paths.
            map = { querySelector: selector => selector === "svg" ? {} : null };
            mutation();
        }
    };
}

test("repeated initialization inserts only one asynchronous provider script", () => {
    const page = fixture();
    page.start();
    page.start();
    assert.equal(page.scripts.length, 1);
    assert.equal(page.scripts[0].async, true);
    assert.equal(page.section.dataset.mapState, "loading");
});

test("a script error gives an unavailable message instead of a visit count", () => {
    const page = fixture();
    page.start();
    page.scripts[0].emit("error");
    assert.equal(page.status.textContent, "Visitor map is temporarily unavailable.");
    assert.equal(page.status.hidden, false);
    assert.equal(page.section.dataset.mapState, "unavailable");
    assert.equal(page.host["aria-busy"], "false");
});

test("timeout can recover when a late map and its background finish loading", () => {
    const page = fixture();
    page.start();
    assert.equal(page.timers.get(1).delay, 15000);
    page.timers.get(1).callback();
    assert.equal(page.section.dataset.mapState, "unavailable");
    page.render();
    assert.equal(page.status.hidden, false);
    page.images[0].emit("load");
    assert.equal(page.section.dataset.mapState, "ready");
    assert.equal(page.status.hidden, true);
    assert.equal(page.timers.size, 0);
    assert.equal(page.scripts.length, 1);
});

test("an SVG alone is not success when the land background fails", () => {
    const page = fixture();
    page.start();
    page.render();
    assert.equal(page.section.dataset.mapState, "loading");
    page.images[0].emit("error");
    assert.equal(page.section.dataset.mapState, "unavailable");
    assert.equal(page.status.hidden, false);
});
