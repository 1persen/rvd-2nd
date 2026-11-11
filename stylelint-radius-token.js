// Stylelint config — Nordic Yellow radius token enforcement.
// Hardcoded `border-radius` values are rejected; use var(--radius-*) tokens.
// Raw values are only allowed when the declaration sits inside a :root token
// definition, e.g. `--radius-md: 4px;` (property starts with --).
const stylelint = require('stylelint');

const allowedTokenValues = new Set([
  'var(--radius-sm)',
  'var(--radius-md)',
  'var(--radius-lg)',
  'var(--radius-xl)',
  'var(--radius-full)',
  '0',
  '0px',
  'inherit',
  'initial',
  'unset',
  'revert',
  'revert-layer',
]);

const ruleName = 'local/radius-token';

const rule = stylelint.createPlugin(ruleName, () => {
  return (root, result) => {
    root.walkDecls((decl) => {
      // Only enforce on `border-radius` itself; skip custom-property definitions
      // like `--radius-md: 4px` (those ARE the token source).
      if (decl.prop !== 'border-radius') return;
      const value = decl.value.trim();
      // allow var(--radius-*) including comma/space-separated multi-value where
      // every part is a radius token (e.g. "0 var(--radius-lg) var(--radius-lg) 0")
      const parts = value.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
      const ok = parts.every(
        (p) => allowedTokenValues.has(p) || p.startsWith('var(--radius-')
      );
      if (!ok) {
        stylelint.utils.report({
          message: 'Use a radius token (var(--radius-sm|md|lg|xl|full)) instead of "' + value + '"',
          node: decl,
          result,
          ruleName,
        });
      }
    });
  };
});

rule.ruleName = ruleName;
module.exports = rule;
