/** randomUUID requires HTTPS, while getRandomValues also works on HTTP IPs. */
export function installUuidFallback(target: Crypto) {
  if (typeof target.randomUUID === 'function') return;
  target.randomUUID = () => {
    const bytes = target.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  };
}

if (typeof globalThis.crypto !== 'undefined') installUuidFallback(globalThis.crypto);
