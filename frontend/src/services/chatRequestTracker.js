export function createChatRequestTracker() {
  let active = null
  let latest = null

  function reset() {
    const previous = active
    active = null
    latest = null
    previous?.controller.abort()
    return previous
  }

  return {
    begin(messageId) {
      if (active) return null
      const ticket = {
        messageId,
        requestId: crypto.randomUUID(),
        controller: new AbortController(),
      }
      active = ticket
      latest = ticket
      return ticket
    },
    isCurrent(ticket) {
      return ticket != null && active === ticket
    },
    isLatest(ticket) {
      return ticket != null && latest === ticket
    },
    finish(ticket) {
      if (ticket == null || active !== ticket) return false
      active = null
      return true
    },
    cancel() {
      return active ? reset() : null
    },
    reset,
  }
}
