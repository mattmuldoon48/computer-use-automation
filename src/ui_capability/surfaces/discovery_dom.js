() => {
    // This fixed helper reads rendered UI only. Element handles remain private to
    // the adapter; IDs may resolve HTML label associations but are never locators.
    const limit = 200;
    const visible = element => {
        const style = getComputedStyle(element);
        const box = element.getBoundingClientRect();
        return !element.closest('[hidden],[inert],[aria-hidden="true"]')
            && style.visibility !== 'hidden' && style.visibility !== 'collapse'
            && style.display !== 'none' && box.width > 0 && box.height > 0;
    };
    const bounded = value => {
        const text = (value || '').replace(/\s+/g, ' ').trim();
        return text.length > 0 && text.length <= 256 ? text : null;
    };
    const text = element => visible(element) ? bounded(element.innerText) : null;
    const ariaName = element => {
        const references = (element.getAttribute('aria-labelledby') || '').trim();
        if (references) {
            const parts = references.split(/\s+/).map(id => document.getElementById(id));
            if (parts.some(part => !part || !visible(part))) return null;
            return bounded(parts.map(part => part.innerText || '').join(' '));
        }
        return bounded(element.getAttribute('aria-label'));
    };
    const literal = value => ({kind: 'literal', value});
    const sectionNames = element => {
        const names = [];
        let ancestors = 0;
        for (let parent = element.parentElement; parent; parent = parent.parentElement) {
            if (++ancestors > 32) throw new Error('unsupported DOM depth');
            if (!visible(parent)) continue;
            let name = null;
            if (parent.tagName === 'FIELDSET') {
                const legends = Array.from(parent.children).filter(child => child.tagName === 'LEGEND');
                if (legends.length === 1) name = text(legends[0]);
                name = ariaName(parent) || name;
            } else if (parent.getAttribute('role') === 'group') {
                name = ariaName(parent);
            } else if (parent.tagName === 'SECTION') {
                const headings = Array.from(parent.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"]'))
                    .filter(heading => heading.closest('section') === parent && visible(heading));
                if (headings.length === 1) name = text(headings[0]);
            }
            if (name && !names.includes(name)) names.push(name);
            if (names.length > 8) throw new Error('unsupported section depth');
        }
        return names;
    };
    const selector = 'a[href],button,input,textarea,select,h1,h2,h3,h4,h5,h6,'
        + '[role="heading"],[role="status"],tr > td';
    const candidates = [];
    for (const element of document.querySelectorAll(selector)) {
        if (!visible(element)) continue;
        const tag = element.tagName.toLowerCase();
        const type = (element.getAttribute('type') || 'text').toLowerCase();
        if (tag === 'input' && !['text', 'search', 'email', 'tel', 'url', 'number', 'submit', 'button'].includes(type)) continue;
        if (['input', 'textarea', 'select', 'button'].includes(tag) && element.disabled) continue;
        if (['input', 'textarea'].includes(tag) && element.readOnly) continue;
        const locators = [];
        const labels = Array.from(element.labels || []).filter(visible).map(text).filter(Boolean);
        const explicit = ariaName(element);
        if (['input', 'textarea', 'select'].includes(tag)) {
            for (const label of [...new Set([...labels, ...(explicit ? [explicit] : [])])]) {
                locators.push({kind: 'label', label: literal(label)});
            }
        }
        const inferred = tag === 'a' ? 'link'
            : tag === 'button' || (tag === 'input' && ['submit', 'button'].includes(type)) ? 'button'
            : tag === 'input' || tag === 'textarea' ? 'textbox'
            : tag === 'select' ? 'combobox'
            : /^h[1-6]$/.test(tag) ? 'heading' : '';
        const role = element.getAttribute('role') || inferred;
        if (labels.length > 8) throw new Error('too many visible labels');
        let name = explicit || bounded(labels.join(' '));
        if (!name && ['button', 'link', 'heading'].includes(role)) {
            name = tag === 'input' ? bounded(element.value) : text(element);
        }
        // Unnamed status regions have an empty accessible name, not their text.
        if (['button', 'link', 'textbox', 'combobox', 'heading', 'status'].includes(role)
            && (name || role === 'status')) {
            locators.push({kind: 'role', role, name: literal(name || '')});
        }
        const cell = element.closest('td');
        const row = cell && cell.parentElement;
        if (row && row.tagName === 'TR'
            && Array.from(row.children).filter(child => child.tagName === 'TD').length === 1) {
            const caption = cell.previousElementSibling;
            const captionText = caption && caption.tagName === 'TH' ? text(caption) : null;
            if (captionText && ['input', 'select', 'button'].includes(tag)) {
                locators.push({kind: 'table_label_control', label: literal(captionText), control: tag});
            } else if (captionText && element === cell
                && !cell.querySelector('input,textarea,select,button,a[href],[contenteditable]')) {
                locators.push({kind: 'table_label_value', label: literal(captionText)});
            }
        }
        if (!locators.length) continue;
        const sections = [null, ...sectionNames(element).map(literal)];
        const specs = sections.flatMap(section => locators.map(locator => ({section, locator})));
        candidates.push({element, specs});
        if (candidates.length > limit) throw new Error('too many visible targets');
    }
    return candidates;
}
