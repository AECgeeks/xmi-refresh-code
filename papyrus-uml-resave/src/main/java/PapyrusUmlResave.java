import java.io.File;
import java.util.HashMap;
import java.util.Map;

import org.eclipse.emf.common.util.URI;
import org.eclipse.emf.ecore.resource.Resource;
import org.eclipse.emf.ecore.resource.ResourceSet;
import org.eclipse.emf.ecore.resource.impl.ResourceSetImpl;
import org.eclipse.emf.ecore.xmi.XMLResource;
import org.eclipse.uml2.uml.resource.UMLResource;
import org.eclipse.uml2.uml.resources.util.UMLResourcesUtil;

public final class PapyrusUmlResave {

    public static void main(String[] args) throws Exception {
        if (args.length < 1 || args.length > 2) {
            System.err.println("Usage:");
            System.err.println("  mvn exec:java -Dexec.args=\"input.uml [output.uml]\"");
            System.exit(2);
        }

        File input = new File(args[0]).getAbsoluteFile();
        File output = args.length == 2
                ? new File(args[1]).getAbsoluteFile()
                : input;

        if (!input.exists()) {
            throw new IllegalArgumentException("Input file does not exist: " + input);
        }

        ResourceSet resourceSet = new ResourceSetImpl();

        // Important: initializes UML2 pathmaps, packages, libraries, and factories
        // for use outside Eclipse.
        UMLResourcesUtil.init(resourceSet);

        resourceSet
            .getResourceFactoryRegistry()
            .getExtensionToFactoryMap()
            .put(UMLResource.FILE_EXTENSION, UMLResource.Factory.INSTANCE);

        URI inputUri = URI.createFileURI(input.getPath());

        System.out.println("Loading: " + input);
        Resource resource = resourceSet.getResource(inputUri, true);

        if (!input.equals(output)) {
            resource.setURI(URI.createFileURI(output.getPath()));
        }

        Map<Object, Object> saveOptions = new HashMap<>();

        saveOptions.put(XMLResource.OPTION_FORMATTED, Boolean.TRUE);
        saveOptions.put(XMLResource.OPTION_ENCODING, "UTF-8");

        // Useful for stable diffs across platforms.
        saveOptions.put(Resource.OPTION_LINE_DELIMITER, "\n");

        System.out.println("Saving:  " + output);
        resource.save(saveOptions);

        System.out.println("Done.");
    }

    private PapyrusUmlResave() {
        // Utility class
    }
}