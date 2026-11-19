function normalizeText(value) {
  return typeof value === 'string'
    ? value.normalize('NFC').replace(/\s+/gu, ' ').trim()
    : ''
}

function descriptionKey(name, description) {
  const normalizedName = normalizeText(name)
  const normalizedDescription = normalizeText(description)
  if (!normalizedName || !normalizedDescription) return null
  return JSON.stringify([normalizedName, normalizedDescription])
}

function photoDescriptionKeys(photos, failedImages) {
  const keys = new Set()
  if (photos?.status !== 'ready' || !Array.isArray(photos.images)) return keys

  for (const image of photos.images) {
    if (failedImages.has(image.image_id) || !Array.isArray(image.contexts)) continue
    for (const context of image.contexts) {
      if (context.mapping_confirmed !== true) continue
      const key = descriptionKey(context.spot_name, context.description)
      if (key !== null) keys.add(key)
    }
  }
  return keys
}

export function selectDetailDescriptions(detail, photos, failedImages = new Set()) {
  const keys = photoDescriptionKeys(photos, failedImages)
  const spots = detail.spots.filter((spot) => (
    !keys.has(descriptionKey(spot.name, spot.description))
  )).map((spot) => ({ ...spot }))

  const facts = detail.accessibility_facts.filter((fact) => {
    const value = normalizeText(fact.raw_value)
    if (!value) return true
    const section = normalizeText(fact.section)
    const label = normalizeText(fact.description)
    // 조사 분류와 항목 이름도 사진 설명에 있어야 같은 정보입니다.
    const fullDescription = [section, label, value].filter(Boolean).join(' ')
    return !keys.has(descriptionKey(fact.facility_name, fullDescription))
  }).map((fact) => ({ ...fact }))

  return {
    spots,
    accessibility_facts: facts,
    hidden_spots: detail.spots.length - spots.length,
    hidden_facts: detail.accessibility_facts.length - facts.length,
  }
}
