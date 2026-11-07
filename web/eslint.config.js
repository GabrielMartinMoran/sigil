import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import tseslint from 'typescript-eslint';

/**
 * Flat config, recommended sets only.
 *
 * The rules that are not part of a preset are the ones that earn their place here:
 * a floating promise in a `useEffect` is the bug this codebase is most likely to
 * have (fetch, streaming), and `no-console` keeps logging out of the client.
 * Formatting is Prettier's job: anything about whitespace is switched off in the
 * last block so the two never argue.
 */
export default tseslint.config(
  { ignores: ['dist', 'node_modules'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommendedTypeChecked],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
      parserOptions: {
        project: ['./tsconfig.json'],
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      '@typescript-eslint/no-floating-promises': 'error',
      '@typescript-eslint/consistent-type-imports': ['error', { prefer: 'type-imports' }],
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
      'no-console': ['error', { allow: ['warn', 'error'] }],
      eqeqeq: ['error', 'smart'],
    },
  },
  {
    files: ['vite.config.ts', 'eslint.config.js', 'scripts/**/*.{ts,mts,js,mjs}'],
    extends: [tseslint.configs.disableTypeChecked],
    languageOptions: {
      globals: globals.node,
      parser: tseslint.parser,
      parserOptions: { project: null },
    },
    rules: {
      // Config and build scripts run in Node and are not part of the typed project.
      '@typescript-eslint/no-floating-promises': 'off',
      'no-console': 'off',
    },
  },
);
