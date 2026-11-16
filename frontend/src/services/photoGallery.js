export const PHOTO_PAGE_SIZE = 16

export const PHOTO_CATEGORIES = [
  { id: 'all', label: '전체' },
  { id: 'parking', label: '주차장' },
  { id: 'entrance', label: '출입구' },
  { id: 'toilet', label: '화장실' },
  { id: 'slope', label: '계단·경사로' },
  { id: 'path', label: '관람동선' },
  { id: 'other', label: '기타·미분류' },
]

const CATEGORY_RULES = [
  ['parking', /주차/u],
  ['entrance', /출입구|출입문|입구|매표소|진입로|접근로/u],
  ['toilet', /화장실|세면|변기/u],
  ['slope', /계단|경사|단차|문턱/u],
  ['path', /동선|관람로|관람길|산책로|산책길|탐방로|보행로|둘레길|이동로/u],
]

function normalizeText(value) {
  return typeof value === 'string'
    ? value.normalize('NFC').replace(/\s+/gu, ' ').trim().toLowerCase()
    : ''
}

function contextsOf(image) {
  return Array.isArray(image?.contexts) ? image.contexts : []
}

export function getPhotoCategoryIds(image) {
  const categories = new Set()
  for (const context of contextsOf(image)) {
    if (context?.mapping_confirmed !== true) continue
    const name = normalizeText(context.spot_name).replace(/\s/gu, '')
    for (const [id, pattern] of CATEGORY_RULES) {
      if (pattern.test(name)) categories.add(id)
    }
  }
  return categories.size > 0 ? [...categories] : ['other']
}

export function getPhotoTitle(image) {
  const seen = new Set()
  const names = []
  for (const context of contextsOf(image)) {
    const key = normalizeText(context?.spot_name)
    if (!key || seen.has(key)) continue
    seen.add(key)
    names.push(context.spot_name)
  }
  if (names.length > 0) return names.join(' · ')
  return normalizeText(image?.caption) ? image.caption : '시설·지점명 미등록'
}

function searchText(image) {
  const values = [image.caption, image.alt]
  for (const context of contextsOf(image)) {
    values.push(context?.spot_name, context?.description)
  }
  return normalizeText(
    values.filter((value) => typeof value === 'string').join(' ')
  ).replace(/\s/gu, '')
}

export function selectPhotoGallery(
  images,
  { category = 'all', query = '', page = 1 } = {},
) {
  const source = Array.isArray(images) ? images.filter((image) => (
    image && typeof image.image_id === 'string' && image.image_id.trim()
  )) : []
  const selectedCategory = PHOTO_CATEGORIES.some((item) => item.id === category)
    ? category : 'all'
  const terms = normalizeText(query).split(' ').filter(Boolean)
  const searched = source.map((image) => ({
    image,
    categories: getPhotoCategoryIds(image),
    text: searchText(image),
  })).filter((item) => terms.every((term) => item.text.includes(term)))

  const categoryCounts = Object.fromEntries(
    PHOTO_CATEGORIES.map((item) => [item.id, 0])
  )
  categoryCounts.all = searched.length
  for (const item of searched) {
    for (const id of item.categories) categoryCounts[id] += 1
  }

  const matchingImages = searched.filter((item) => (
    selectedCategory === 'all' || item.categories.includes(selectedCategory)
  )).map((item) => item.image)
  const pageCount = Math.ceil(matchingImages.length / PHOTO_PAGE_SIZE)
  const requestedPage = Number.isSafeInteger(page) && page > 0 ? page : 1
  const currentPage = Math.min(requestedPage, Math.max(1, pageCount))
  const offset = (currentPage - 1) * PHOTO_PAGE_SIZE

  return {
    totalCount: source.length,
    matchingImages,
    pageImages: matchingImages.slice(offset, offset + PHOTO_PAGE_SIZE),
    categoryCounts,
    category: selectedCategory,
    page: currentPage,
    pageCount,
  }
}