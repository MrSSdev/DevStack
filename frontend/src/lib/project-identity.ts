export type ProjectIdentityDraft = {
  name: string
  // null means automatic. A string, even empty, is a deliberate edit.
  slug: string | null
  domain: string | null
}

export function slugFromName(name: string): string {
  // NFKD does not decompose Polish ł; handle it explicitly.
  const normalized = name.toLowerCase().replace(/ł/g, 'l')
    .normalize('NFKD').replace(/\p{M}/gu, '')
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  if (!normalized) return ''
  // The daemon requires a leading ASCII letter and at most 40 characters.
  const prefixed = /^[0-9]/.test(normalized) ? 'projekt-' + normalized : normalized
  return prefixed.slice(0, 40).replace(/-+$/g, '')
}

export function resolveProjectIdentity(draft: ProjectIdentityDraft) {
  const slug = draft.slug ?? slugFromName(draft.name)
  const domain = draft.domain ?? (slug.trim() ? slug.trim().toLowerCase() + '.test' : '')
  return { slug, domain }
}

export function projectIdentityPayload(draft: ProjectIdentityDraft) {
  const { slug, domain } = resolveProjectIdentity(draft)
  return { name: draft.name.trim(), slug: slug.trim(), domain: domain.trim().toLowerCase() }
}
