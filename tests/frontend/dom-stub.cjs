function node(properties = {}) {
  const attributes = {};
  const classes = new Set();
  return {
    dataset: {}, hidden: false, disabled: false, textContent: "", attributes,
    setAttribute: (name, value) => { attributes[name] = String(value); },
    getAttribute: (name) => attributes[name],
    classList: {
      add: (...values) => values.forEach((value) => classes.add(value)),
      remove: (...values) => values.forEach((value) => classes.delete(value)),
      contains: (value) => classes.has(value),
      toggle: (value, force) => {
        const enabled = force === undefined ? !classes.has(value) : force;
        if (enabled) classes.add(value); else classes.delete(value);
        return enabled;
      },
    },
    ...properties,
  };
}

module.exports = {node};
