// Adds the gateway API key to same-origin fetches and asks for it on a 401.
// The key lives only in this browser's localStorage. Loopback never needs one.
(function () {
  const KEY = 'gateway_api_key';
  const get = () => { try { return localStorage.getItem(KEY) || ''; } catch (e) { return ''; } };
  const set = (v) => { try { localStorage.setItem(KEY, v); } catch (e) {} };
  const orig = window.fetch.bind(window);
  async function withKey(input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const sameOrigin = url.startsWith('/') || url.startsWith(location.origin);
    if (!sameOrigin) return orig(input, init);
    const key = get();
    init = Object.assign({}, init);
    const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
    if (key && !headers.has('Authorization')) headers.set('Authorization', 'Bearer ' + key);
    init.headers = headers;
    return orig(input, init);
  }
  window.fetch = async function (input, init) {
    let resp = await withKey(input, init);
    if (resp.status === 401) {
      const entered = window.prompt('This gateway needs an API key:');
      if (entered) { set(entered.trim()); resp = await withKey(input, init); }
    }
    return resp;
  };
})();
