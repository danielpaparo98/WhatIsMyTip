const TEAM_LOGOS: Record<string, string> = {
  'Adelaide': 'Adelaide.png',
  'Brisbane Lions': 'Brisbane.png',
  'Carlton': 'Carlton.png',
  'Collingwood': 'Collingwood.png',
  'Essendon': 'Essendon.png',
  'Fremantle': 'Fremantle.png',
  'Geelong': 'Geelong.png',
  'Gold Coast': 'GoldCoast.png',
  'Greater Western Sydney': 'Giants.png',
  'Hawthorn': 'Hawthorn.png',
  'Melbourne': 'Melbourne.png',
  'North Melbourne': 'NorthMelbourne.png',
  'Port Adelaide': 'PortAdelaide.png',
  'Richmond': 'Richmond.png',
  'St Kilda': 'StKilda.png',
  'Sydney': 'Sydney.png',
  'West Coast': 'WestCoast.png',
  'Western Bulldogs': 'Bulldogs.png',
}

export function useTeamLogos() {
  const getLogoUrl = (teamName: string): string => {
    const filename = TEAM_LOGOS[teamName] || ''
    return filename ? `/logos/${filename}` : ''
  }

  return { getLogoUrl, TEAM_LOGOS }
}
