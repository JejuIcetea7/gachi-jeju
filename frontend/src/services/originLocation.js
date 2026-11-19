import { loadKakaoMaps } from './kakaoMaps.js'
import { getCurrentCoordinates } from './geolocation.js'

export const CENTER_ORIGIN_NAME = '제주빅데이터센터'
export const CENTER_ORIGIN_ADDRESS = '제주특별자치도 제주시 첨단로 241'

let centerOriginPromise = null

function requestGeocoder(maps, runRequest) {
  return new Promise((resolve, reject) => {
    const timeoutId = window.setTimeout(() => {
      reject(new Error('주소 확인 시간이 초과됐습니다. 다시 시도해 주세요.'))
    }, 10000)

    function handleResult(rows, status) {
      window.clearTimeout(timeoutId)

      if (
        status === maps.services.Status.OK &&
        Array.isArray(rows) && rows.length > 0
      ) {
        resolve(rows)
      } else {
        reject(new Error('주소 정보를 확인하지 못했습니다. 장소 검색을 이용해 주세요.'))
      }
    }

    try {
      runRequest(handleResult)
    } catch {
      window.clearTimeout(timeoutId)
      reject(new Error('주소 정보를 조회하지 못했습니다. 다시 시도해 주세요.'))
    }
  })
}

async function loadCenterOrigin() {
  const maps = await loadKakaoMaps()
  const geocoder = new maps.services.Geocoder()
  const rows = await requestGeocoder(maps, (callback) => {
    geocoder.addressSearch(CENTER_ORIGIN_ADDRESS, callback, {
      analyze_type: maps.services.AnalyzeType.EXACT,
    })
  })

  const row = rows[0]
  if (
    String(row?.x ?? '').trim() === '' ||
    String(row?.y ?? '').trim() === ''
  ) {
    throw new Error('센터 좌표를 확인하지 못했습니다. 장소 검색을 이용해 주세요.')
  }

  const latitude = Number(row.y)
  const longitude = Number(row.x)

  if (
    !Number.isFinite(latitude) || Math.abs(latitude) > 90 ||
    !Number.isFinite(longitude) || Math.abs(longitude) > 180
  ) {
    throw new Error('센터 좌표를 확인하지 못했습니다. 장소 검색을 이용해 주세요.')
  }

  return {
    id: 'jeju-bigdata-center',
    name: CENTER_ORIGIN_NAME,
    address: row.road_address?.address_name
      || row.address?.address_name
      || CENTER_ORIGIN_ADDRESS,
    latitude,
    longitude,
    source: 'center',
  }
}

export function getCenterOrigin() {
  if (!centerOriginPromise) {
    centerOriginPromise = loadCenterOrigin().catch((error) => {
      centerOriginPromise = null
      throw error
    })
  }

  return centerOriginPromise
}

export async function getCurrentOrigin() {
  const coordinates = await getCurrentCoordinates()
  let address = '주소 확인 필요'

  try {
    const maps = await loadKakaoMaps()
    const geocoder = new maps.services.Geocoder()
    const rows = await requestGeocoder(maps, (callback) => {
      geocoder.coord2Address(
        coordinates.longitude,
        coordinates.latitude,
        callback,
      )
    })

    address = rows[0]?.road_address?.address_name
      || rows[0]?.address?.address_name
      || address
  } catch {
    address = '주소 확인 필요'
  }

  return {
    id: 'current-location',
    name: '현재 위치',
    address,
    ...coordinates,
    source: 'geolocation',
  }
}