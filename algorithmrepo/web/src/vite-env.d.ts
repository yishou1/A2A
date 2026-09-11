/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_AMOS_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
