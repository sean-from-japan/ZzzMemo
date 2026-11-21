// Stands in for localStorage inside a stored HTML page. The page runs in a
// sandbox with no origin, where the real localStorage throws. The server
// inserts this before the page's own scripts with what the page saved last
// time; each change is posted to the app, which saves it for this item.
// Opened on its own (not in the app's reading screen), changes last only
// until the page is closed.
(function (item, data) {
  var has = function (k) { return Object.prototype.hasOwnProperty.call(data, k); };
  var send = function () {
    if (item && window.parent !== window) window.parent.postMessage({ zzzPageState: { item: item, data: data } }, "*");
  };
  var store = {
    getItem: function (k) { k = String(k); return has(k) ? data[k] : null; },
    setItem: function (k, v) { data[String(k)] = String(v); send(); },
    removeItem: function (k) { k = String(k); if (has(k)) { delete data[k]; send(); } },
    clear: function () { data = {}; send(); },
    key: function (i) { var ks = Object.keys(data); return i < ks.length ? ks[i] : null; },
    get length() { return Object.keys(data).length; },
  };
  try { Object.defineProperty(window, "localStorage", { value: store, configurable: true }); } catch (e) {}
})
