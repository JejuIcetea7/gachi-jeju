export function getCurrentCoordinates() {
  return new Promise((resolve, reject) => {
    if (!window.isSecureContext) {
      reject(new Error(
        '현재 연결에서는 위치 기능을 사용할 수 없습니다. 장소 검색을 이용해 주세요.',
      ))
      return
    }

    if (!navigator.geolocation) {
      reject(new Error(
        '이 브라우저에서는 현재 위치를 사용할 수 없습니다. 장소 검색을 이용해 주세요.',
      ))
      return
    }

    function handleSuccess(position) {
      const latitude = position?.coords?.latitude
      const longitude = position?.coords?.longitude
      const accuracy = position?.coords?.accuracy

      if (
        !Number.isFinite(latitude) || Math.abs(latitude) > 90 ||
        !Number.isFinite(longitude) || Math.abs(longitude) > 180
      ) {
        reject(new Error('위치 좌표를 확인하지 못했습니다. 다시 시도해 주세요.'))
        return
      }

      resolve({
        latitude,
        longitude,
        accuracy_m: Number.isFinite(accuracy) && accuracy >= 0
          ? accuracy
          : null,
      })
    }

    function handleError(error) {
      let message = '현재 위치를 확인할 수 없습니다. 장소 검색을 이용해 주세요.'

      if (error.code === 1) {
        message = '위치 권한이 허용되지 않았습니다. 브라우저에서 허용하거나 장소 검색을 이용해 주세요.'
      } else if (error.code === 3) {
        message = '위치 확인 시간이 초과됐습니다. 다시 시도하거나 장소 검색을 이용해 주세요.'
      }

      reject(new Error(message))
    }

    try {
      navigator.geolocation.getCurrentPosition(
        handleSuccess,
        handleError,
        {
          enableHighAccuracy: true,
          timeout: 15000,
          maximumAge: 0,
        },
      )
    } catch {
      reject(new Error('현재 위치를 확인할 수 없습니다. 장소 검색을 이용해 주세요.'))
    }
  })
}