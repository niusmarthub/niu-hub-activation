import './niu-toilets-card.js';
import './niu-dashboard-card.js';
import './niu-bottom-card.js';
import './niu-hub-card.js';
import './niu-schedules-card.js';
import {tablet} from './shared.js';

class NiuTabletClimate extends HTMLElement {
  setConfig(config) {
    this.lang = config.lang === 'en' ? 'en' : 'fr';
    this.style.display = 'grid';
    this.style.gap = '8px';
    this.style.gridTemplateColumns = 'repeat(auto-fit, minmax(min(100%, 260px), 1fr))';
  }

  set hass(hass) {
    this.latest = hass;
    const config = tablet(hass);
    const entities = Array.from({length: config?.options.climate_count || 0},
      (_, index) => config.climate[String(index + 1)]?.thermostat || null);
    const key = JSON.stringify([this.lang, entities]);
    if (this.key !== key) {
      this.key = key;
      this.replaceChildren();
      this.renderThermostats(entities, key);
    }
    for (const card of this.children) card.hass = hass;
  }

  async renderThermostats(entities, key) {
    if (!entities.length) return;
    try {
      const helpers = await window.loadCardHelpers();
      if (this.key !== key) return;
      entities.forEach((entity, index) => {
        const name = (this.lang === 'en' ? 'Air Conditioner ' : 'Climatiseur ') + (index + 1);
        const card = helpers.createCardElement(entity
          ? {type: 'thermostat', entity, name}
          : {type: 'markdown', content: '**' + name + '**\n\n' + (this.lang === 'en' ? 'Thermostat unavailable' : 'Thermostat indisponible')});
        card.hass = this.latest;
        this.append(card);
      });
    } catch {
      if (this.key === key) this.textContent = this.lang === 'en' ? 'Thermostats unavailable' : 'Thermostats indisponibles';
    }
  }
}

customElements.define('niu-tablet-climate-card', NiuTabletClimate);
