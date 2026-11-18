import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useWorkspaceStore } from '../stores/workspace.js';
export function useNavigation() {
  const route = useRoute(),
    router = useRouter(),
    store = useWorkspaceStore();
  const isConsult = computed(() => route.name === 'consult');
  const section = computed(() => String(route.params.section || 'chat'));
  function navigate(path) {
    store.ui.menu = false;
    store.closeDialog();
    return router.push(path);
  }
  const go = (view) => navigate(
    ['chat', 'visuals'].includes(view)
      ? `/cases/${store.state.activeId}/${view}`
      : `/cases/${store.state.activeId}/chat?panel=${encodeURIComponent(view)}`
  );
  const selectCase = (id) => navigate(`/cases/${id}/chat`);
  const consult = () => navigate('/consult');
  return { isConsult, section, go, selectCase, consult, route };
}
