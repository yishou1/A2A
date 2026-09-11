/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ALGOLIB_API_PREFIX?: string
  readonly VITE_AMOS_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
