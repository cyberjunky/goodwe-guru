import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      // React Compiler strictness rules (react-hooks v7). The codebase predates
      // them — fetch-on-mount effects, helpers declared below their effect, the
      // EnergyFlow canvas reading refs — none of which is a runtime bug here.
      // Kept visible as warnings; tighten when refactoring a page.
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/immutability': 'warn',
      'react-hooks/refs': 'warn',
      // Context + hook exports alongside the provider are deliberate.
      'react-refresh/only-export-components': 'off',
    },
  },
])
