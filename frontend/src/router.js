import { createRouter, createWebHistory } from 'vue-router';
import { useWorkspaceStore } from './stores/workspace.js';
import { TABS } from './domain/presentation.js';
import CaseWorkspace from './views/CaseWorkspace.vue';
import ConsultationView from './views/ConsultationView.vue';
export function createWorkspaceRouter(pinia) {
  const store = useWorkspaceStore(pinia);
  const home = () => `/cases/${store.state.activeId}/chat`;
  const router = createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/', redirect: home },
      { path: '/consult', name: 'consult', component: ConsultationView },
      {
        path: '/cases/:caseId/:section?',
        name: 'case',
        component: CaseWorkspace
      },
      { path: '/:pathMatch(.*)*', redirect: home }
    ]
  });
  router.beforeEach((to) => {
    store.ui.menu = false;
    store.closeDialog();
    if (to.name !== 'case') return;
    if (!store.selectCase(String(to.params.caseId))) return home();
    if (
      ![...TABS.map((t) => t[0]), 'review', 'results'].includes(
        to.params.section
      )
    )
      return home();
  });
  return router;
}
