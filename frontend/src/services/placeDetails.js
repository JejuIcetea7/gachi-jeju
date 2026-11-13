export function groupAccessibilityFacts(facts) {
  const groups = new Map()

  for (const fact of facts) {
    const key = JSON.stringify([fact.section, fact.facility_name])
    let group = groups.get(key)
    if (!group) {
      group = {
        section: fact.section,
        facility_name: fact.facility_name,
        facts: [],
      }
      groups.set(key, group)
    }
    group.facts.push({ ...fact })
  }

  return [...groups.values()]
}
