let sdkPromise = null

function isKakaoMapsReady() {
  const maps = window.kakao?.maps

  return Boolean(
    maps?.Map && maps.services?.Places && maps.services?.Geocoder,
  )
}

export function loadKakaoMaps() {
  const apiKey = import.meta.env.VITE_KAKAO_MAP_KEY?.trim()

  if (!apiKey) {
    return Promise.reject(
      new Error('지도용 JavaScript 키를 설정해 주세요.'),
    )
  }

  if (isKakaoMapsReady()) {
    return Promise.resolve(window.kakao.maps)
  }

  if (sdkPromise) {
    return sdkPromise
  }

  sdkPromise = new Promise((resolve, reject) => {
    const script = document.createElement('script')
    let isSettled = false

    const timeoutId = window.setTimeout(() => {
      handleFailure('지도 연결 시간이 초과됐습니다. 다시 시도해 주세요.')
    }, 15000)

    function handleFailure(message) {
      if (isSettled) {
        return
      }

      isSettled = true
      window.clearTimeout(timeoutId)
      script.onload = null
      script.onerror = null
      script.remove()
      reject(new Error(message))
    }

    const params = new URLSearchParams({
      appkey: apiKey,
      autoload: 'false',
      libraries: 'services',
    })

    script.src = `https://dapi.kakao.com/v2/maps/sdk.js?${params}`
    script.async = true

    script.onload = () => {
      if (isSettled) {
        return
      }

      if (!window.kakao?.maps?.load) {
        handleFailure('지도를 불러오지 못했습니다. 지도 설정을 확인해 주세요.')
        return
      }

      try {
        window.kakao.maps.load(() => {
          if (isSettled) {
            return
          }

          if (!isKakaoMapsReady()) {
            handleFailure('지도 초기화에 실패했습니다.')
            return
          }

          isSettled = true
          window.clearTimeout(timeoutId)
          script.onload = null
          script.onerror = null
          resolve(window.kakao.maps)
        })
      } catch {
        handleFailure('지도 초기화에 실패했습니다.')
      }
    }

    script.onerror = () => {
      handleFailure('지도를 불러오지 못했습니다. 연결 상태를 확인해 주세요.')
    }

    document.head.appendChild(script)
  }).catch((error) => {
    sdkPromise = null
    throw error
  })

  return sdkPromise
}