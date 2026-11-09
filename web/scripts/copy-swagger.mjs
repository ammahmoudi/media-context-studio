import { copyFile, mkdir } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
const require = createRequire(import.meta.url)
const source = dirname(require.resolve('swagger-ui-dist/package.json'))
const target = new URL('../../video_context/static/swagger/', import.meta.url)
await mkdir(target, { recursive: true })
for (const file of ['swagger-ui-bundle.js', 'swagger-ui.css', 'LICENSE', 'favicon-32x32.png']) {
  await copyFile(join(source, file), new URL(file, target))
}
