import { useEffect, useId, useRef } from 'react'
import {
  PHOTO_CATEGORIES,
  PHOTO_PAGE_SIZE,
  getPhotoTitle,
  selectPhotoGallery,
} from './services/photoGallery.js'

function text(value, fallback) {
  return typeof value === 'string' && value.trim() ? value : fallback
}

function PhotoCaption({ image }) {
  return (
    <figcaption className="place-photo-caption">
      {image.contexts?.length > 0 ? image.contexts.map((context, index) => (
        <div className="place-photo-context" key={index}>
          <strong>{text(context.spot_name, '시설·지점명 미등록')}</strong>
          {!context.mapping_confirmed && (
            <span className="place-photo-unconfirmed">장소 연결 미확인</span>
          )}
          <p>{text(context.description, '설명 없음')}</p>
        </div>
      )) : <p>{text(image.caption, '설명 없음')}</p>}
    </figcaption>
  )
}

function PlacePhotoGallery({
  images,
  failedImages,
  view,
  onViewChange,
  onImageError,
}) {
  const searchId = useId()
  const searchRef = useRef(null)
  const detailTitleRef = useRef(null)
  const tileRefs = useRef(new Map())
  const pendingFocus = useRef(null)
  const result = selectPhotoGallery(images, view)
  const selectedIndex = result.matchingImages.findIndex(
    (image) => image.image_id === view.selectedId
  )
  const selected = result.matchingImages[selectedIndex]

  useEffect(() => {
    const target = pendingFocus.current
    pendingFocus.current = null
    if (target === 'detail') {
      detailTitleRef.current?.focus()
    } else if (target) {
      tileRefs.current.get(target)?.focus()
    }
  }, [view.selectedId, result.page])

  function updateView(changes) {
    onViewChange((previous) => ({ ...previous, ...changes }))
  }

  function showPhoto(imageId) {
    pendingFocus.current = 'detail'
    updateView({ selectedId: imageId })
  }

  function showList() {
    pendingFocus.current = selected.image_id
    updateView({
      selectedId: null,
      page: Math.floor(selectedIndex / PHOTO_PAGE_SIZE) + 1,
    })
  }

  function movePhoto(offset) {
    const next = result.matchingImages[selectedIndex + offset]
    if (next) updateView({ selectedId: next.image_id })
  }

  function resetFilters() {
    updateView({ category: 'all', query: '', page: 1, selectedId: null })
    searchRef.current?.focus()
  }

  if (selected) {
    return (
      <section className="place-photo-viewer" aria-label="선택한 사진">
        <div className="place-photo-viewer-controls">
          <button type="button" className="secondary-button" onClick={showList}>
            사진 목록
          </button>
          <span className="place-photo-position" role="status">
            사진 {selectedIndex + 1} / {result.matchingImages.length}
          </span>
          <div className="place-photo-step-buttons">
            <button
              type="button"
              className="secondary-button"
              onClick={() => movePhoto(-1)}
              disabled={selectedIndex === 0}
            >
              이전 사진
            </button>
            <button
              type="button"
              className="secondary-button"
              onClick={() => movePhoto(1)}
              disabled={selectedIndex === result.matchingImages.length - 1}
            >
              다음 사진
            </button>
          </div>
        </div>

        <h3 ref={detailTitleRef} tabIndex={-1} className="place-photo-selected-title">
          {getPhotoTitle(selected)}
        </h3>
        <figure className="place-photo-selected">
          {failedImages.has(selected.image_id) ? (
            <p className="place-image-message">
              이 사진을 불러오지 못했습니다. 아래 설명을 확인해 주세요.
            </p>
          ) : (
            <img
              src={selected.src}
              alt={text(selected.alt, getPhotoTitle(selected))}
              decoding="async"
              onError={() => onImageError(selected.image_id)}
            />
          )}
          <PhotoCaption image={selected} />
        </figure>
        {!failedImages.has(selected.image_id) && (
          <a
            className="place-photo-open-link"
            href={selected.src}
            target="_blank"
            rel="noopener noreferrer"
          >
            사진 새 탭에서 크게 열기
          </a>
        )}
      </section>
    )
  }

  return (
    <section className="place-photo-browser" aria-label="사진 목록">
      <div className="place-photo-search">
        <label htmlFor={searchId}>시설명·설명으로 사진 찾기</label>
        <div className="place-photo-search-controls">
          <input
            id={searchId}
            ref={searchRef}
            type="search"
            value={view.query}
            placeholder="예: 주차장, 경사로"
            onChange={(event) => updateView({
              query: event.target.value, page: 1, selectedId: null,
            })}
          />
          {view.query && (
            <button type="button" className="secondary-button" onClick={() => {
              updateView({ query: '', page: 1, selectedId: null })
              searchRef.current?.focus()
            }}>
              검색 지우기
            </button>
          )}
        </div>
      </div>

      <div className="place-photo-categories" role="group" aria-label="사진 주제">
        {PHOTO_CATEGORIES.map((category) => (
          <button
            key={category.id}
            type="button"
            aria-pressed={result.category === category.id}
            disabled={category.id !== 'all' && result.categoryCounts[category.id] === 0}
            onClick={() => updateView({
              category: category.id, page: 1, selectedId: null,
            })}
          >
            {category.label} <span>({result.categoryCounts[category.id]})</span>
          </button>
        ))}
      </div>

      <div className="place-photo-list-toolbar">
        <p role="status">
          전체 {result.totalCount}장 중 {result.matchingImages.length}장
        </p>
        {result.pageCount > 1 && (
          <nav className="place-photo-pagination" aria-label="사진 목록 페이지">
            <button
              type="button"
              className="secondary-button"
              disabled={result.page === 1}
              onClick={() => updateView({ page: result.page - 1 })}
            >
              이전
            </button>
            <span>{result.page} / {result.pageCount} 페이지</span>
            <button
              type="button"
              className="secondary-button"
              disabled={result.page === result.pageCount}
              onClick={() => updateView({ page: result.page + 1 })}
            >
              다음
            </button>
          </nav>
        )}
      </div>

      {result.matchingImages.length === 0 ? (
        <div className="place-image-message">
          <p>선택한 분류나 검색어에 맞는 사진이 없습니다.</p>
          <button type="button" className="secondary-button" onClick={resetFilters}>
            전체 사진 보기
          </button>
        </div>
      ) : (
        <ul className="place-photo-grid">
          {result.pageImages.map((image) => (
            <li key={image.image_id}>
              <button
                type="button"
                className="place-photo-tile"
                ref={(element) => {
                  if (element) tileRefs.current.set(image.image_id, element)
                  else tileRefs.current.delete(image.image_id)
                }}
                aria-label={getPhotoTitle(image) + ' 사진 크게 보기'}
                onClick={() => showPhoto(image.image_id)}
              >
                {failedImages.has(image.image_id) ? (
                  <span className="place-photo-preview is-failed">사진 불러오기 실패</span>
                ) : (
                  <img
                    className="place-photo-preview"
                    src={image.src}
                    alt=""
                    loading="lazy"
                    decoding="async"
                    onError={() => onImageError(image.image_id)}
                  />
                )}
                <span className="place-photo-tile-title">{getPhotoTitle(image)}</span>
                {image.contexts?.some((context) => !context.mapping_confirmed) && (
                  <span className="place-photo-unconfirmed">장소 연결 미확인</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export default PlacePhotoGallery