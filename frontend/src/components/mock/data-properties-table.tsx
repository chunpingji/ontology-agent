interface DataProperty {
  iri: string | null;
  label: string;
  value: string;
}

interface DataPropertiesTableProps {
  properties: DataProperty[];
}

export function DataPropertiesTable({ properties }: DataPropertiesTableProps) {
  if (properties.length === 0) return null;

  return (
    <div className="mt-2 space-y-1">
      {properties.map((dp, i) => (
        <div key={i} className="flex gap-2 text-sm">
          <span className="font-medium text-muted-foreground">{dp.label}:</span>
          <span className="text-foreground">{dp.value}</span>
        </div>
      ))}
    </div>
  );
}
