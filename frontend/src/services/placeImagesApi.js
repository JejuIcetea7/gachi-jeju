const REQUEST_TIMEOUT_MS = 15_000

function imageError(message, code, status = null) {
  const error = new Error(message)
  error.name = 'PlaceImagesApiError'
  error.code = code
  error.status = status
  return error
}

function isImageResponse(data, placeId) {
  return data?.place_id === placeId
    && typeof data.name === 'string'
    && data.name.trim().length > 0
    && Array.isArray(data.images)
    && data.images.every((image) => (
      /^[0-9a-f]{64}$/.test(image?.image_id)
      && typeof image?.url === 'string'
      && typeof image?.caption === 'string'
      && typeof image?.alt === 'string'
    ))
}

export async function requestPlaceImages(placeId, { signal } = {}) {
  if (typeof placeId !== 'string' || !placeId.trim()) {
    throw imageError('사진을 조회할 장소를 확인해 주세요.', 'invalid_request')
  }

  const id = placeId.trim()
  const endpoint = `/api/places/${encodeURIComponent(id)}/images`
  signal?.throwIfAborted()

  const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS)
  const requestSignal = signal ? AbortSignal.any([signal, timeout]) : timeout

  try {
    const response = await fetch(endpoint, { signal: requestSignal })

    let data
    try {
      data = await response.json()
    } catch (error) {
      if (requestSignal.aborted) throw error
      throw imageError('사진 정보를 읽지 못했습니다.', 'invalid_response')
    }

    if (!response.ok) {
      throw imageError(
        typeof data?.error?.message === 'string' && data.error.message.trim()
          ? data.error.message
          : '사진을 불러오지 못했습니다.',
        typeof data?.error?.code === 'string' ? data.error.code : 'api_error',
        response.status,
      )
    }

    if (!isImageResponse(data, id)) {
      throw imageError('사진 정보의 형식이 올바르지 않습니다.', 'invalid_response')
    }

    return {
      place_id: data.place_id,
      name: data.name,
      images: data.images.map((image) => ({
        image_id: image.image_id,
        caption: image.caption,
        alt: image.alt,
        src: `${endpoint}/${image.image_id}`,
      })),
    }
  } catch (error) {
    if (signal?.aborted) throw error

    if (timeout.aborted) {
      throw imageError(
        '사진 조회 시간이 초과됐습니다. 다시 시도해 주세요.',
        'client_timeout',
      )
    }

    if (error?.name === 'PlaceImagesApiError') throw error

    throw imageError(
      '사진 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.',
      'network_error',
    )
  }
}