export const tablet = hass => {
  const config = hass?.states?.['sensor.niu_tablet_config'];
  return config?.state === 'ready' ? config.attributes : null;
};

export function mappings(data, type, number) {
  return (data?.mappings || []).filter(item => item.function_type === type
    && (number === undefined || Number(item.function_number) === number));
}

export const association = (data, type, number) => mappings(data, type, number)[0];
export const entity = (data, type, number) => association(data, type, number)?.entity_id;
export const state = (hass, id) => hass?.states?.[id]?.state;
export const availableSwitch = (hass, id) => /^switch\.[a-z0-9_]+$/.test(id ?? '')
  && ['on', 'off'].includes(state(hass, id));

export const escape = value => String(value).replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[c]));

export function switchState(hass, id, action) {
  if (tablet(hass) && availableSwitch(hass, id)) {
    return hass.callService('switch', action, {entity_id: id});
  }
}