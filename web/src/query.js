import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
export function useQuery(key, fallback = '') {
  const route = useRoute(), router = useRouter()
  return computed({ get: () => String(route.query[key] ?? fallback),
    set: value => router.replace({ query: { ...route.query, [key]: value || undefined, page: undefined } }) })
}
