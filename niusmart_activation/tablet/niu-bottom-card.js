import {association, availableSwitch, entity, escape, mappings, state, switchState, tablet} from "./shared.js";

const STYLE = `<style>
        niu-tablet-bottom-card {
          display:block;
          width:100%;
        }

        .wrap {
          width:100%;
          padding:6px;
          box-sizing:border-box;
        }

        .grid {
          display:grid;
          grid-template-columns:1fr 1fr;
          gap:8px;
          margin-bottom:8px;
        }

        .card {
          width:100%;
          box-sizing:border-box;
          border:1px solid var(--divider-color);
          border-radius:8px;
          padding:10px;
          background:var(--ha-card-background, var(--card-background-color));
        }

        .title {
          font-size:15px;
          font-weight:800;
          margin-bottom:8px;
        }

        .status {
          font-size:14px;
          font-weight:800;
          margin-bottom:8px;
        }

        .ok {
          color:#12b977;
        }

        .bad {
          color:#ef4444;
        }

        .row {
          display:grid;
          grid-template-columns:1fr auto;
          gap:8px;
          align-items:center;
          padding:6px 0;
          border-bottom:1px solid var(--divider-color);
          font-size:12px;
        }

        .row:last-child {
          border-bottom:0;
        }

        .buttons {
          display:grid;
          grid-template-columns:1fr 1fr;
          gap:6px;
          margin-top:8px;
        }

        .light-line {
          margin-top:10px;
          padding-top:8px;
          border-top:1px solid var(--divider-color);
        }

        .light-name {
          display:flex;
          justify-content:space-between;
          gap:8px;
          align-items:center;
          font-size:12px;
          margin-bottom:5px;
        }

        button {
          min-height:34px;
          border-radius:5px;
          font-size:11px;
          font-weight:800;
          cursor:pointer;
        }

        .green {
          background:#124d35;
          color:white;
          border:1px solid #25845f;
        }

        .red {
          background:#5a2025;
          color:white;
          border:1px solid #a84444;
        }

        @media (max-width:700px) {
          .grid {
            grid-template-columns:1fr;
          }

          .card {
            padding:7px;
          }
        }
      </style>`;

class NiuBottomCard extends HTMLElement {
  setConfig(config) {
    this.config = config || {};
    this._lang = String(this.config.lang || this.config.language || 'fr').toLowerCase() === 'en' ? 'en' : 'fr';
  }

  _t(fr, en) {
    return this._lang === 'en' ? en : fr;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    const data = tablet(this._hass);
    if (!data) {
      this.replaceChildren();
      return;
    }
    const {options} = data;
    const read = id => state(this._hass, id);
    const mapped = type => entity(data, type);
    const cards = [];
    const actions = [];
    const button = (label, ids, action, color) => {
      const index = actions.push([ids, action]) - 1;
      const disabled = !ids.some(id => availableSwitch(this._hass, id));
      return '<button data-action="' + index + '" class="' + color + '"' + (disabled ? ' disabled' : '') + '>'
        + escape(label) + '</button>';
    };
    const buttons = (on, off, ids) => '<div class="buttons">' + button(on, ids, 'turn_on', 'green')
      + button(off, ids, 'turn_off', 'red') + '</div>';
    const card = (name, content) => '<div class="card"><div class="title">' + escape(name) + '</div>' + content + '</div>';
    const row = (name, status) => '<div class="row"><span>' + escape(name) + '</span><strong>'
      + escape(status) + '</strong></div>';
    const closedState = (id, on = 'on') => {
      const value = read(id);
      if (!['on', 'off'].includes(value)) return '—';
      return value === on ? this._t('FERMÉE', 'CLOSED') : this._t('OUVERTE', 'OPEN');
    };

    const contact = association(data, 'block_closed_sensor');
    const block = mapped('block_control');
    if (contact || block) cards.push(card(this._t('INSTALLATION SANITAIRE', 'SANITARY INSTALLATION'),
      row(this._t('État', 'Status'), closedState(contact?.entity_id, contact?.custom_name === 'off' ? 'off' : 'on'))
      + buttons(this._t('FERMER LE BLOC', 'CLOSE BLOCK'), this._t('OUVRIR LE BLOC', 'OPEN BLOCK'), [block])));
    if (options.door_count > 0) {
      const rows = Array.from({length: options.door_count}, (_, index) => {
        const door = association(data, 'door', index + 1);
        return row(door?.custom_name || this._t('Porte ', 'Door ') + (index + 1), closedState(door?.entity_id));
      });
      cards.push(card(this._t('ÉTAT DES PORTES', 'DOOR STATUS'), rows.join('')));
    }
    if (options.has_exterior_lighting === 1) {
      const lighting = mappings(data, 'exterior_lighting');
      const rows = lighting.map((item, index) => {
        const status = read(item.entity_id);
        return row(item.custom_name || this._t('Lumière ', 'Light ') + (index + 1),
          status === 'on' ? this._t('ALLUMÉE', 'ON') : status === 'off' ? this._t('ÉTEINTE', 'OFF') : '—')
          + buttons(this._t('ALLUMER', 'TURN ON'), this._t('ÉTEINDRE', 'TURN OFF'), [item.entity_id]);
      });
      cards.push(card(this._t('ÉCLAIRAGE EXTÉRIEUR', 'EXTERIOR LIGHTING'),
        buttons(this._t('ALLUMER TOUT', 'TURN ALL ON'), this._t('ÉTEINDRE TOUT', 'TURN ALL OFF'),
          [...new Set(lighting.map(item => item.entity_id))]) + rows.join('')));
    }
    if (options.has_water_control === 1) {
      const opened = read(mapped('water_open_confirm'));
      const closed = read(mapped('water_closed_confirm'));
      const valid = [opened, closed].every(value => ['on', 'off'].includes(value));
      const status = !valid ? '—' : opened === 'on' && closed === 'on' ? this._t('ERREUR CAPTEURS', 'SENSOR ERROR')
        : opened === 'on' ? this._t('OUVERTE', 'OPEN') : closed === 'on' ? this._t('FERMÉE', 'CLOSED')
          : this._t('EN MOUVEMENT', 'MOVING');
      cards.push(card(this._t("ARRIVÉE D’EAU", 'WATER SUPPLY'), row(this._t('État', 'Status'), status)
        + buttons(this._t('FERMER', 'CLOSE'), this._t('OUVRIR', 'OPEN'), [mapped('water_control')])));
    }
    this.innerHTML = STYLE + '<div class="wrap"><div class="grid">' + cards.join('') + '</div></div>';
    this.querySelectorAll('[data-action]').forEach(el => {
      el.onclick = () => {
        const [ids, action] = actions[Number(el.dataset.action)];
        for (const id of ids) switchState(this._hass, id, action);
      };
    });
  }
}

customElements.define('niu-tablet-bottom-card', NiuBottomCard);