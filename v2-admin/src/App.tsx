import React, { useState, useEffect } from 'react';
import { CRM_V2 } from './api';
import { 
  Users, 
  MessageSquare, 
  AlertCircle, 
  Settings, 
  Search,
  UserCheck,
  Globe,
  Bell,
  RefreshCw
} from 'lucide-react';

const App = () => {
  const [activeTab, setActiveTab] = useState('identity');
  const [health, setHealth] = useState<any>(null);

  useEffect(() => {
    CRM_V2.getHealth()
      .then(res => setHealth(res.data))
      .catch(err => console.error("Middleware not connected"));
  }, []);

  return (
    <div className="flex min-h-screen bg-[#0a0c10] text-[#f0f6fc]">
      {/* Sidebar */}
      <aside className="w-64 border-r border-[#30363d] p-6 flex flex-direction-column">
        <div className="mb-10">
          <h1 className="text-xl font-bold text-[#58a6ff] flex items-center gap-2">
            <Globe className="w-6 h-6" />
            Rahma V2
          </h1>
          <p className="text-xs text-[#8b949e] mt-1">Admin Control Center</p>
        </div>

        <nav className="space-y-2 flex-1">
          <NavItem 
            icon={<Users size={18} />} 
            label="Identity Resolver" 
            active={activeTab === 'identity'} 
            onClick={() => setActiveTab('identity')} 
          />
          <NavItem 
            icon={<MessageSquare size={18} />} 
            label="Copy Library" 
            active={activeTab === 'copy'} 
            onClick={() => setActiveTab('copy')} 
          />
          <NavItem 
            icon={<AlertCircle size={18} />} 
            label="Handoff Queue" 
            active={activeTab === 'handoff'} 
            onClick={() => setActiveTab('handoff')} 
            badge="3"
          />
          <NavItem 
            icon={<Settings size={18} />} 
            label="System Config" 
            active={activeTab === 'settings'} 
            onClick={() => setActiveTab('settings')} 
          />
        </nav>

        <div className="mt-auto pt-6 border-t border-[#30363d]">
          <div className="flex items-center gap-3">
            <div className={`w-3 h-3 rounded-full ${health ? 'bg-[#3fb950]' : 'bg-[#f85149]'}`} />
            <span className="text-sm font-medium">{health ? 'Middleware Connected' : 'Middleware Offline'}</span>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 p-10 overflow-y-auto">
        <header className="flex justify-between items-center mb-10">
          <div>
            <h2 className="text-2xl font-semibold">
              {activeTab === 'identity' && 'Identity Merge & Deduplication'}
              {activeTab === 'copy' && 'Deterministic Copy Library'}
              {activeTab === 'handoff' && 'Active Handoff Queue'}
              {activeTab === 'settings' && 'System Settings'}
            </h2>
            <p className="text-[#8b949e]">Rahma Traveler Operations Layer</p>
          </div>
          <button className="p-2 rounded-full hover:bg-[#21262d] relative">
            <Bell size={20} />
            <span className="absolute top-0 right-0 w-2 h-2 bg-[#f85149] rounded-full" />
          </button>
        </header>

        <div className="space-y-8 animate-in fade-in duration-500">
          {activeTab === 'identity' && <IdentityView />}
          {activeTab === 'copy' && <Placeholder title="Copy Library Coming Soon" />}
          {activeTab === 'handoff' && <HandoffView />}
        </div>
      </main>
    </div>
  );
};

const NavItem = ({ icon, label, active, onClick, badge }: any) => (
  <button 
    onClick={onClick}
    className={`w-full flex items-center justify-between px-4 py-3 rounded-lg transition-all ${
      active ? 'bg-[#21262d] text-[#f0f6fc] border border-[#30363d]' : 'text-[#8b949e] hover:text-[#f0f6fc] hover:bg-[#161b22]'
    }`}
  >
    <div className="flex items-center gap-3">
      {icon}
      <span className="font-medium text-sm">{label}</span>
    </div>
    {badge && (
      <span className="bg-[#f85149] text-white text-[10px] px-1.5 py-0.5 rounded-full font-bold">
        {badge}
      </span>
    )}
  </button>
);

const IdentityView = () => {
  const [duplicates, setDuplicates] = useState<any[]>([]);
  const [selectedCluster, setSelectedCluster] = useState<any | null>(null);
  const [masterId, setMasterId] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const fetchDuplicates = () => {
    setLoading(true);
    CRM_V2.getDuplicates()
      .then((res) => {
        const groups = res.data.duplicates || [];
        setDuplicates(groups);
        if (groups.length > 0) {
          setSelectedCluster(groups[0]);
          // Default master to first traveler
          setMasterId(groups[0].records[0]?.traveler_id || '');
        } else {
          setSelectedCluster(null);
          setMasterId('');
        }
      })
      .catch((err) => {
        console.error(err);
        setMessage({ type: 'error', text: 'Failed to connect to middleware database.' });
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchDuplicates();
  }, []);

  const selectCluster = (cluster: any) => {
    setSelectedCluster(cluster);
    setMessage(null);
    setMasterId(cluster.records[0]?.traveler_id || '');
  };

  const handleMerge = () => {
    if (!selectedCluster || !masterId) return;
    
    const aliasIds = selectedCluster.records
      .map((r: any) => r.traveler_id)
      .filter((id: string) => id !== masterId);

    if (aliasIds.length === 0) {
      setMessage({ type: 'error', text: 'You must have at least one alias traveler to merge.' });
      return;
    }

    setSubmitting(true);
    setMessage(null);
    
    CRM_V2.resolveIdentity({ masterId, aliasIds })
      .then((res) => {
        setMessage({
          type: 'success',
          text: `Successfully resolved identity! ${aliasIds.length} profiles merged into ${masterId}.`,
        });
        // Refresh duplicates after 2.5 seconds
        setTimeout(() => {
          fetchDuplicates();
        }, 2000);
      })
      .catch((err) => {
        console.error(err);
        const errMsg = err.response?.data?.error || 'An error occurred during identity resolution.';
        setMessage({ type: 'error', text: errMsg });
      })
      .finally(() => setSubmitting(false));
  };

  // Filter duplicate clusters based on search query
  const filteredClusters = duplicates.filter((group) => {
    const query = searchQuery.toLowerCase();
    if (!query) return true;
    const phoneMatch = group.match_key.includes(query);
    const nameMatch = group.records.some((r: any) => 
      r.full_name?.toLowerCase().includes(query) || r.traveler_id?.toLowerCase().includes(query)
    );
    return phoneMatch || nameMatch;
  });

  return (
    <div className="grid grid-cols-3 gap-8">
      {/* Left Columns - Cluster List */}
      <div className="col-span-2 space-y-6">
        <div className="bg-[#161b22] border border-[#30363d] rounded-xl overflow-hidden">
          <div className="p-4 border-b border-[#30363d] flex justify-between items-center bg-[#0d1117]">
            <span className="text-sm font-semibold uppercase tracking-wider text-[#8b949e]">
              Duplicate Phone Clusters ({filteredClusters.length})
            </span>
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#8b949e]" />
              <input 
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search phone or name..." 
                className="bg-[#0a0c10] border border-[#30363d] rounded-md pl-10 pr-4 py-1.5 text-sm outline-none focus:border-[#58a6ff] w-64 text-[#f0f6fc]"
              />
            </div>
          </div>

          {loading ? (
            <div className="p-20 text-center text-[#8b949e] flex flex-direction-column items-center gap-4 justify-center">
              <RefreshCw className="animate-spin w-8 h-8 text-[#58a6ff]" />
              <p>Scanning database for duplicate identity lookup keys...</p>
            </div>
          ) : filteredClusters.length === 0 ? (
            <div className="p-20 text-center text-[#8b949e]">
              <Users className="w-12 h-12 mx-auto mb-4 opacity-40 text-[#58a6ff]" />
              <h4 className="font-bold text-white text-lg">No Duplicates Found</h4>
              <p className="text-sm mt-1">Excellent! SQLite database is fully consolidated and clean.</p>
            </div>
          ) : (
            <div className="divide-y divide-[#30363d] max-h-[500px] overflow-y-auto">
              {filteredClusters.map((group) => {
                const isSelected = selectedCluster?.match_key === group.match_key;
                const namesList = group.records.map((r: any) => r.full_name).join(', ');
                return (
                  <div 
                    key={group.match_key}
                    onClick={() => selectCluster(group)}
                    className={`p-5 cursor-pointer transition-all hover:bg-[#1f242b] flex justify-between items-center ${
                      isSelected ? 'bg-[#1f242b] border-l-4 border-l-[#58a6ff]' : ''
                    }`}
                  >
                    <div className="space-y-1.5">
                      <div className="flex items-center gap-3">
                        <span className="font-bold text-[#58a6ff] font-mono">Cluster Key: {group.match_key}</span>
                        <span className="bg-[#f85149]/15 text-[#f85149] text-xs px-2 py-0.5 rounded-full font-bold">
                          {group.count} Travelers
                        </span>
                      </div>
                      <p className="text-xs text-[#8b949e] max-w-xl truncate">
                        <strong className="text-white">Profiles:</strong> {namesList}
                      </p>
                    </div>
                    <ArrowRight className={`w-5 h-5 text-[#8b949e] transition-transform ${isSelected ? 'translate-x-1 text-[#58a6ff]' : ''}`} />
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Right Column - Merge Form */}
      <div className="space-y-6">
        <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-6 shadow-xl sticky top-6">
          <h3 className="font-semibold mb-5 flex items-center gap-2 border-b border-[#30363d] pb-3 text-white">
            <UserCheck className="text-[#3fb950]" />
            Merge Linker
          </h3>

          {message && (
            <div className={`p-4 rounded-lg text-sm mb-4 border ${
              message.type === 'success' 
                ? 'bg-[#1b2a1a] text-[#3fb950] border-[#2b5e2b]' 
                : 'bg-[#2d1b1c] text-[#f85149] border-[#6b2c2d]'
            }`}>
              {message.text}
            </div>
          )}

          {!selectedCluster ? (
            <div className="text-center py-10 text-[#8b949e]">
              <AlertCircle className="w-10 h-10 mx-auto mb-3 opacity-30 text-[#8b949e]" />
              <p className="text-sm">Select a candidate group from the list to initiate resolution.</p>
            </div>
          ) : (
            <div className="space-y-5">
              <div className="bg-[#0d1117] p-4 rounded-lg border border-[#30363d]">
                <span className="block text-[10px] text-[#8b949e] uppercase font-bold tracking-wider mb-2">
                  Active Phone Lookup Key
                </span>
                <code className="text-sm font-mono text-[#58a6ff] bg-[#161b22] px-2.5 py-1 rounded">
                  {selectedCluster.match_key}
                </code>
              </div>

              <div>
                <label className="block text-xs text-[#8b949e] mb-2 uppercase font-bold tracking-wider">
                  Select Master Profile (Keep)
                </label>
                <div className="space-y-2.5 max-h-[250px] overflow-y-auto pr-1">
                  {selectedCluster.records.map((r: any) => {
                    const isSelectedMaster = masterId === r.traveler_id;
                    const tripsCount = r.total_trips || 0;
                    return (
                      <div 
                        key={r.traveler_id}
                        onClick={() => setMasterId(r.traveler_id)}
                        className={`p-3.5 rounded-lg border cursor-pointer transition-all flex items-start gap-3 ${
                          isSelectedMaster 
                            ? 'bg-[#1b263b] border-[#58a6ff]' 
                            : 'bg-[#0d1117] border-[#30363d] hover:border-[#8b949e]/50'
                        }`}
                      >
                        <input 
                          type="radio" 
                          name="masterSelection"
                          checked={isSelectedMaster}
                          onChange={() => setMasterId(r.traveler_id)}
                          className="w-4 h-4 mt-0.5 accent-[#58a6ff]"
                        />
                        <div className="flex-1 min-w-0">
                          <div className="flex justify-between items-center">
                            <span className="font-bold text-sm text-white truncate block">{r.full_name}</span>
                            <span className="text-[10px] bg-[#21262d] px-2 py-0.5 rounded text-[#8b949e] font-mono">
                              {r.traveler_id}
                            </span>
                          </div>
                          <div className="flex items-center gap-3 mt-1.5 text-xs text-[#8b949e]">
                            <span>Trips: <strong className="text-[#c9d1d9]">{tripsCount}</strong></span>
                            <span>•</span>
                            <span className={`px-1.5 py-0.2 rounded-full font-semibold ${
                              r.status === 'VIP' ? 'bg-[#d29922]/15 text-[#d29922]' : 'bg-[#238636]/15 text-[#3fb950]'
                            }`}>
                              {r.status || 'Active'}
                            </span>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="bg-[#0d1117]/80 p-3.5 rounded-lg border border-[#30363d] text-xs text-[#8b949e] space-y-1">
                <span className="text-white font-bold block mb-1">Deduplication Rule:</span>
                <p>The selected master profile's name, email, and identity data will be preserved.</p>
                <p className="text-[#e1e4e8]">
                  All bookings, lead stages, and interactions from checked aliases will be merged transactionally. Alias accounts will be set to <code className="text-[#f85149] bg-[#f85149]/10 px-1 rounded">Merged</code>.
                </p>
              </div>

              <button 
                onClick={handleMerge}
                disabled={submitting}
                className="w-full bg-[#238636] hover:bg-[#2ea043] disabled:bg-[#238636]/50 text-white py-3 rounded-lg font-bold transition-all shadow-lg shadow-green-900/10 flex justify-center items-center gap-2"
              >
                {submitting ? (
                  <>
                    <RefreshCw className="animate-spin w-4 h-4" />
                    <span>Resolving Identities...</span>
                  </>
                ) : (
                  <span>Finalize Merge Resolution</span>
                )}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

// Simple visual helper components
const ArrowRight = ({ className }: { className?: string }) => (
  <svg className={className} xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="2" stroke="currentColor" width="20" height="20">
    <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
  </svg>
);

const HandoffView = () => (
  <div className="grid grid-cols-1 gap-4">
    <HandoffCard 
      user="Karim Safwat" 
      reason="DUPLICATE_PHONE_LOOKUP" 
      priority="HIGH"
      time="2 mins ago"
    />
    <HandoffCard 
      user="Anonymous User" 
      reason="BLOCKED (Agent stuck)" 
      priority="MEDIUM"
      time="15 mins ago"
    />
  </div>
);

const HandoffCard = ({ user, reason, priority, time }: any) => (
  <div className="bg-[#161b22] border border-[#30363d] p-6 rounded-xl flex justify-between items-center group hover:border-[#58a6ff]/50 transition-all">
    <div className="flex gap-4">
      <div className={`w-12 h-12 rounded-full flex items-center justify-center ${
        priority === 'HIGH' ? 'bg-[#f85149]/10 text-[#f85149]' : 'bg-[#d29922]/10 text-[#d29922]'
      }`}>
        <AlertCircle size={24} />
      </div>
      <div>
        <h4 className="font-bold text-lg">{user}</h4>
        <p className="text-sm text-[#8b949e]">{reason}</p>
        <span className="text-[10px] text-[#8b949e] uppercase mt-2 inline-block">{time}</span>
      </div>
    </div>
    <button className="bg-[#21262d] hover:bg-[#30363d] px-6 py-2 rounded-lg font-semibold text-sm border border-[#30363d]">
      Assume Control
    </button>
  </div>
);

const Placeholder = ({ title }: any) => (
  <div className="bg-[#161b22] border border-dashed border-[#30363d] rounded-xl p-20 text-center">
    <div className="w-16 h-16 bg-[#21262d] rounded-full flex items-center justify-center mx-auto mb-6">
      <Settings className="text-[#8b949e] animate-spin-slow" />
    </div>
    <h3 className="text-xl font-bold">{title}</h3>
    <p className="text-[#8b949e] mt-2">I am building this module in the next turn.</p>
  </div>
);

export default App;
