export type InstallerField = {
  id:string
  label:string
  default:string
  choices:{id:string;label:string}[]
  kits:string[]
  when:Record<string,string[]>
}
export type InstallerValues = Record<string,string>

// Process fields in the adapter's declared dependency order. Hidden fields
// are excluded entirely so switching authentication or component style
// cannot accidentally submit a previously selected, incompatible option.
export function resolveInstallerFields(schema:InstallerField[],kit:string,requested:InstallerValues) {
  const fields:InstallerField[]=[]
  const values:InstallerValues={}
  for(const field of schema){
    if(field.kits.length&&!field.kits.includes(kit))continue
    if(Object.entries(field.when).some(([key,allowed])=>!allowed.includes(values[key]??'')))continue
    const proposed=requested[field.id]
    values[field.id]=field.choices.some(choice=>choice.id===proposed)?proposed:field.default
    fields.push(field)
  }
  return {fields,values}
}
export function frameworkWebroot(framework:{webroot:string}|undefined,manual:string):string {
  return framework?.webroot??manual
}
